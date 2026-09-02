# Teams Concurrency Design — Keeping Each Case's Context Isolated

## The problem

When multiple FNOL emails arrive around the same time, each spawns an
independent Foundry Agent **run** and an independent **Teams alert**. If all
those alerts are posted as plain messages into the *same* Teams channel with
no structural separation, two things go wrong:

1. **Reply ambiguity** — if an adjuster replies "looks good, assign to John,"
   it's ambiguous *which* case they're replying to, both for a human reading
   the channel and for any bot trying to programmatically correlate the
   reply back to a specific Foundry thread/case.
2. **Interleaved follow-ups** — if the orchestrator posts a multi-turn
   clarification ("do you want me to also check subrogation?") for case A,
   and a new alert for case B lands in between, the next human reply in the
   channel could be misattributed to the wrong case if correlation is done
   naively (e.g., "last message in the channel").

## The fix: one Teams **reply chain (thread)** per case, correlated via a
persistent case→thread mapping table

### 1. Post every case as a **new top-level channel message**, never append to an existing one

- Each new email/case gets its **own top-level message** in the target Teams
  channel (via Graph `POST /teams/{team-id}/channels/{channel-id}/messages`).
  This message becomes the **root of a reply chain** — Teams natively renders
  all subsequent replies to that specific message as a nested, visually
  separated thread under it.
- Capture the returned message's **`id`** (the root message ID) immediately.

### 2. Persist a `case_id -> team_id, channel_id, root_message_id, foundry_thread_id` mapping

- Every time a new case alert is posted, write one row to a small durable
  store (a **Fabric SQL Database**, reusing the same Fabric workspace as
  Fabric IQ — see
  `shared/case_thread_store_schema.sql` for a minimal schema) keyed by a
  **case identifier** you mint at intake time (e.g., a GUID, or the claim
  number once Fabric IQ assigns one).
- This mapping is the single source of truth correlating:
  - the **Foundry Agent thread** (`thread_id`) holding the full conversation
    history/context for that case, and
  - the **Teams reply-chain root** (`root_message_id`) where all human-facing
    communication about that case lives.

```sql
-- shared/case_thread_store_schema.sql (excerpt)
CREATE TABLE CaseThreadMap (
    CaseId            NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TeamId            NVARCHAR(64)  NOT NULL,
    ChannelId         NVARCHAR(128) NOT NULL,
    RootMessageId     NVARCHAR(128) NOT NULL,
    FoundryThreadId   NVARCHAR(128) NOT NULL,
    CreatedUtc        DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
    LastActivityUtc   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);
```

### 3. All follow-up activity replies **into the existing thread**, never posts new top-level messages for the same case

- If the orchestrator needs to post an update for an *existing* case
  (e.g., a delayed subrogation check finishes, or a human asks a follow-up
  question that gets routed back through Foundry), the Logic App/webapp/Function path:
  1. Looks up `CaseThreadMap` by `CaseId` (or by matching the incoming
     Teams message's **`replyToId`**, which Teams sets on any reply within
     an existing thread — see step 4).
  2. Posts via Graph `POST /teams/{team-id}/channels/{channel-id}/messages/{root-message-id}/replies`
     — this nests the new message under the correct case's thread instead of
     creating a new root message.
  3. Uses the mapped `foundry_thread_id` (not a new thread) when calling back
     into the Foundry Agent Service, so the agent has full prior context for
     that case.

### 4. Correlating an inbound human **reply** back to the right case

- When a human replies inside Teams (e.g., a bot/webhook receives the reply
  via a registered Bot Framework messaging endpoint, or a Logic App polls via
  Graph `GET .../messages/{root-message-id}/replies`), the inbound payload
  includes:
  - `replyToId` (the ID of the root/parent message this is a reply to), and
  - `channelIdentity.channelId` / the team/channel it was posted in.
- Look up `CaseThreadMap` by `(TeamId, ChannelId, RootMessageId = replyToId)`
  to deterministically resolve the case — **no ambiguity**, regardless of how
  many other cases have concurrent alerts open in the same channel, because
  Teams itself guarantees `replyToId` identifies the exact thread the human
  clicked "Reply" on.
- Then call back into Foundry using that case's `foundry_thread_id` so the
  agent's response is grounded in that case's full history, not a fresh/empty
  context.

### 5. Concurrency safety at the data-store level

- Use an **idempotent, atomic insert** (e.g., an `INSERT` guarded by the
  `CaseId` primary key in the Fabric SQL Database, which fails naturally on a
  duplicate key) when creating a new `CaseThreadMap` row, so that if the
  trigger somehow fires twice for the same email (rare but possible with
  at-least-once delivery semantics in Graph webhooks or connector retries),
  you don't create two competing Teams threads for the same case.
- Recommended `CaseId` derivation: prefer a **stable value from the email
  itself** (e.g., Graph message `internetMessageId`, or a hash of
  `from + subject + receivedDateTime` if `internetMessageId` isn't reliably
  unique in your tenant's mail flow) rather than a fresh GUID minted after
  the fact — this makes the whole pipeline naturally idempotent/safe to
  retry without extra dedup logic.

### 6. Foundry-side context isolation (already native, just don't misuse it)

- Foundry Agent Service threads are **already fully isolated per `thread_id`**
  — the agent has no visibility into other threads unless you explicitly
  share content. The **only** risk of context bleed is an *application bug*
  where the trigger logic accidentally reuses one `thread_id` for multiple
  concurrent emails (e.g., a cached/global variable instead of a per-case
  lookup). The `CaseThreadMap` table in step 2 is precisely what prevents this: always mint a **new** Foundry thread per new case, and always **look up** (never guess/reuse) the existing thread for follow-ups. In the current reference implementation, `workflow-teams-reply-poller.json` also runs with trigger concurrency forced to `runs: 1` so two poll cycles cannot race to post into the same Foundry thread while a prior run is still active.

## Summary flow

```
New email (case X) arrives
  │
  ├─> Mint CaseId (e.g., internetMessageId hash)
  ├─> POST /threads (Foundry)              -> foundry_thread_id
  ├─> Run orchestrator agent on thread
  ├─> POST new top-level Teams message      -> root_message_id
  └─> INSERT CaseThreadMap(CaseId, team, channel, root_message_id, foundry_thread_id)

Human replies in Teams under case X's message
  │
  ├─> Inbound payload has replyToId = root_message_id
  ├─> SELECT CaseThreadMap WHERE RootMessageId = replyToId  -> resolves CaseId + foundry_thread_id
  ├─> POST message to foundry_thread_id (same Foundry thread, full history preserved)
  └─> POST reply to .../messages/{root_message_id}/replies  (nests under the same Teams thread)
```

This guarantees that **N concurrent emails produce N independent Teams
threads and N independent Foundry threads**, and any reply — no matter how
many other cases are "in flight" at the same time — is deterministically
routed back to the correct case's context on both the Teams side and the
Foundry side.

