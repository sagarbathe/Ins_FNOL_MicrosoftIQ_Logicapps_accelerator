# Agent Governance & Observability — MVP

Scope: the minimal viable implementation of the "Agent governance (Agent 365)" and
"Observability and auditing" control planes from the Microsoft IQ pattern slide, applied to
this accelerator's actual components (`auto-fnol-triage-orchestrator` agent, the
`svc-fnol-agent` Agent Identity, and its 3 tools: Fabric IQ, Foundry IQ, Work IQ). Both
capabilities below use **preview** features/APIs - acceptable per this accelerator's current
stance ("preview is fine as long as it works").

Full governance (Purview DLP/eDiscovery/communication compliance, Defender threat detection,
Agent 365 dashboards/Sentinel correlation) is **explicitly out of scope for this MVP** and
noted as Phase 2 below.

---

## 1. Agent governance — onboarding to the Agent 365 registry

Microsoft is converging agent visibility into one registry under **Agent 365**
(Microsoft 365 admin center), while **Microsoft Entra Agent ID** continues to own agent
*identity* (sign-in, Conditional Access, identity governance). See
[Agent Registry convergence with Microsoft Agent 365](https://learn.microsoft.com/en-us/entra/agent-id/agent-registry-convergence).

### 1.1 Background: how this agent already gets an Entra Agent ID

Azure AI Foundry's Agent Service automatically provisions an **agent identity** (backed by an
Entra Agent ID blueprint) for every agent it creates - no manual app registration is needed.
This already happened implicitly when `foundry/create_orchestrator_agent.py` created
`auto-fnol-triage-orchestrator`. The MVP work here is **making that identity visible and
governed**, not creating it.

### 1.2 MVP steps

1. **View the registry.**
   Sign in to [admin.microsoft.com](https://admin.microsoft.com/) → left nav **Agents** →
   **All Agents** → **Registry**.
2. **Find this solution's agent(s).**
   Use the **Platform** filter (Azure AI Foundry / Copilot Studio, depending on what the portal
   shows) and the **Channel** filter (**Teams**, since this agent was published to Teams) to
   narrow the list to `auto-fnol-triage-orchestrator`.
   - If it shows under **Unmanaged agents** (agents operating without Agent 365's governance),
     that is the concrete signal this MVP step is meant to close.
3. **Assign an owner/sponsor.**
   Set a named human owner on the agent entry (not a shared mailbox) - this is the
   "sponsor accountability" control the slide calls out, and is required before any
   lifecycle/access-review automation can apply to the agent.
4. **Review its Entra Agent ID identity.**
   In the [Microsoft Entra admin center](https://entra.microsoft.com/), locate the Agent ID
   tied to `auto-fnol-triage-orchestrator` (Identity → Agent ID, preview). Confirm it is a
   distinct, named identity - not sharing credentials with the `svc-fnol-agent` Agent Identity
   used for delegated Graph/Fabric/SQL calls (those are two different identities in this
   architecture: the Foundry agent's own Entra Agent ID vs. the dedicated human-like
   `svc-fnol-agent` user used for delegated auth).
5. **Apply least-privilege Conditional Access to `svc-fnol-agent`.**
   Since this identity never needs interactive sign-in (only used by the two Logic Apps and
   the Work IQ webapp), add a Conditional Access policy that blocks interactive/browser
   sign-in for this account and scopes allowed sign-in to the expected service context.
6. **Document the governed tool allow-list.**
   Record the 3 tools wired into the orchestrator (Fabric IQ read-only ontology query,
   Foundry IQ read-only knowledge agent, Work IQ mixed read/send-email/post-Teams) as the
   baseline in this doc (see table below). Any future tool/connection addition must update
   `foundry/create_orchestrator_agent.py` and this table together - never added ad hoc in the
   Foundry portal.

| Tool | Access level | Why it's allowed |
|---|---|---|
| Fabric IQ (`fabric_iq_ontology_queryOntology`) | Read-only | Structured Policy/Claim/Adjuster/FraudSignal lookups only; no write path exists |
| Foundry IQ (`foundry_iq_knowledge_agent`, connected agent) | Read-only | Governed knowledge base (policy wording, SIU playbook); no write path exists |
| Work IQ (`triggers/webapp/app.py` routes) | Mixed read/write | Read: document/mail search, ontology proxy, case lookups. Write: Teams posting, escalation email send - both gated in `ORCHESTRATOR_INSTRUCTIONS` to fire only on explicit human request, never inferred |

### 1.3 Lifecycle & ALM rule (process, no tooling change)

**No live edit to `ORCHESTRATOR_INSTRUCTIONS` (or any tool wiring) without a corresponding git
commit, on the same day, pushed to `main` and tagged** (see `v1.1.0` for the established
pattern: edit `foundry/create_orchestrator_agent.py` → run
`python foundry/update_orchestrator_agent.py` to push live → commit + tag). This keeps the
git history as the single source of truth for what the live agent is actually instructed to
do, independent of whatever Agent 365's own change-history view eventually shows.

### 1.4 Explicitly deferred to Phase 2

- Purview: unified audit log / DSPM for AI, DLP, eDiscovery, communication compliance.
- Defender: threat detection / incident correlation in Defender XDR.
- Agent 365 fleet-wide dashboards and Sentinel correlation of agent/identity/data signals.

These all require tenant-level Purview/Defender/Sentinel licensing and onboarding decisions
beyond this accelerator's scope - revisit once the registry/identity basics above are in place
and in use.

---

## 2. Observability and auditing — MVP (Option A)

### 2.1 What's already in place

- **Run history / agent analytics**: Logic Apps run history (per-action HTTP request/response
  for both `la-fnol-email-intake` and `la-fnol-teams-reply-poller`) plus
  `foundry/inspect_run_trace.py` (reads the Foundry Threads/Runs/RunSteps API directly) - this
  combination already covers both the outer orchestration-call audit trail and the inner
  tool-call trace, including for Teams/email-originated runs that the Foundry portal's Traces
  tab does not show (see `docs/detailed-call-sequence.md` Known Issue #10).

### 2.2 What this MVP adds: webapp exception logging

**Problem found this session**: every route in `triggers/webapp/app.py` caught all exceptions
and returned only `jsonify({"error": str(e)}), 500` - the real traceback was never logged
anywhere, which is why the Fabric-capacity-paused outage (`"unhandled errors in a TaskGroup"`)
took direct Python reproduction to root-cause instead of a log lookup.

**Fix applied** (zero new dependencies/Azure resources - "Option A"): every route's
`except Exception as e:` block now also calls `app.logger.exception(...)` with the relevant
request context (query, team/channel ids, case id, etc.) before returning the same
`{"error": str(e)}, 500` response. `app.logger` is configured at module load
(`logging.basicConfig(level=logging.INFO, ...)`), and Flask/gunicorn write that logger's output
to stdout/stderr, which Azure App Service's built-in logging captures automatically - no SDK,
no new app setting, no redeploy-breaking change.

### 2.3 Where the logs land and how to read them

| Need | How |
|---|---|
| Live tail while reproducing an issue | `az webapp log tail --name app-autofnol-workiq --resource-group RG_AutoFNOL_Logicapps` |
| Live tail via portal | App Service `app-autofnol-workiq` → **Monitoring → Log stream** |
| Logs from a past request (not just live) | Enable **Application Logging (Filesystem)** under the App Service's **App Service logs** blade if not already on, then browse `https://app-autofnol-workiq.scm.azurewebsites.net/api/vfs/LogFiles/` (Kudu) |

**Known limitation (by design, for MVP)**: filesystem logs rotate on a small quota - this is
good enough for "what just broke" during/just after an incident, but is **not** a durable
audit trail and is **not** KQL-queryable or alertable. If/when that's needed, upgrade to
**Option B**: add `azure-monitor-opentelemetry` to `triggers/webapp/requirements.txt`, point it
at an Application Insights connection string app setting, and the same exceptions land in that
workspace's `AppExceptions`/`AppTraces` tables (queryable with `az monitor log-analytics
query`, alertable, 90-day retention by default) - no further code changes needed beyond adding
the instrumentation call at app startup.

### 2.4 Redeploy required

This change is in the webapp's source only - it takes effect on the next
`python triggers/webapp/deploy_webapp.py` run (or any other redeploy of
`app-autofnol-workiq`). It has not been deployed automatically as part of writing this doc.

---

## 3. Run-history persistence table (`AgentRunHistory`)

### 3.1 What it is

`AgentRunHistory` is a Fabric SQL table in the **same database that already hosts
`CaseThreadMap`** - chosen over a Fabric Lakehouse/Delta table because the data is small,
relational, and point-queried (per-case/per-thread lookups), not analytical/Spark-scale, and
reusing the existing DB avoids a second connection/auth path. It persists the same
Threads/Runs/RunSteps trace that `foundry/inspect_run_trace.py` prints to the console, as
structured rows: one row per user message, per agent message, and per tool call, including
`ParentThreadId` links for connected-agent sub-threads (e.g. Foundry IQ's internal sub-thread
spawned when the orchestrator calls it as a tool - see `docs/detailed-call-sequence.md` Known
Issue #10 for why those sub-threads are otherwise easy to mistake for the main conversation).

Schema: `shared/agent_run_history_schema.sql`. Columns: `CaseId`, `ThreadId`,
`ParentThreadId`, `RunId`, `StepId`, `StepIndex`, `RecordType` (`user_message` /
`agent_message` / `tool_call`), `ToolName`, `ToolType`, `Prompt`, `Response`, `RunStatus`,
`ErrorMessage`, `AssistantId`, `PromptTokens`, `CompletionTokens`, `SourceChannel`,
`CreatedAtUtc`, `IngestedAtUtc`. Idempotent upserts are enforced by two filtered unique
indexes keyed on `(ThreadId, RunId, StepId)` and `(ThreadId, RunId)` (the latter for the
synthetic user-message row, which has no `StepId`).

### 3.2 How it's built

- **`shared/create_agent_run_history_table.py`** - one-time (idempotent) table/index creation.
  Run once: `python shared/create_agent_run_history_table.py`.
- **`foundry/sync_run_history.py`** - the ingestion script. Reads Foundry Threads/Runs/RunSteps
  the same way `foundry/inspect_run_trace.py` does (`AgentsClient` + `AzureCliCredential` - run
  `az login` first), and writes to Fabric SQL using the `svc-fnol-agent` Agent Identity's
  SQL-scoped token (same pattern as `shared/create_case_thread_map.py` - run
  `shared/bootstrap_agent_identity_tokens.py` first if you haven't already). `CaseId` and
  `SourceChannel` are filled in via a lookup against `CaseThreadMap` by `FoundryThreadId`
  (`'case'` if found, `'unknown'` otherwise - e.g. Playground test threads, or a
  connected-agent sub-thread before its parent is synced). `ParentThreadId` is backfilled in a
  second pass once a `connected_agent` tool call reveals the child thread it spawned.

  ```
  cd C:\Sagar\MicrosoftIQ\Ins_FNOL_MicrosoftIQ_Logicapps_accelerator
  python foundry\sync_run_history.py                    # sync the 30 most recent threads
  python foundry\sync_run_history.py --limit 100         # sync more threads
  python foundry\sync_run_history.py thread_abc123        # sync just one thread
  ```

  Re-running it is always safe - already-synced rows are skipped. There is no scheduled
  trigger for this yet (manual/on-demand only); adding one (e.g. a timer-triggered Logic App or
  Azure Function) is a natural Phase 2 follow-up if/when ongoing automatic sync is needed.

### 3.3 Verified

Synced the two known threads from this session's "is it hallucinating?" investigation
(`thread_cJOjUBko3x9rjLa3Jowr4bbX` and its connected-agent sub-thread
`thread_bqM4gOE2Ir3QPOgTlhHHJDNL`) and confirmed in the table: the parent/child link via
`ParentThreadId`, correct `ToolName` values (`fabric_iq_ontology_queryOntology`,
`foundry_iq_knowledge_agent`, `azure_ai_search`), and `CaseId`/`SourceChannel` populated from
`CaseThreadMap` for the parent thread. Re-running the sync against the same threads inserted
zero additional rows, confirming idempotency.
