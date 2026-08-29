# Design Options — Replacing Copilot Studio + Power Automate

Goal: keep **Fabric IQ**, **Foundry IQ**, and **Work IQ** exactly as they are
today, but move the **orchestrator/triage "brain"** into **Azure AI Foundry**
and remove **Power Automate** as the email-trigger/glue layer.

This is broken into two independent decisions:

- **A. Where does the orchestrator live and how is it invoked?**
- **B. What replaces the Power Automate email trigger + glue logic?**

Pick one option from each section; they compose independently.

---

## A. Orchestrator: Azure AI Foundry Agent Service

Regardless of which trigger option (B) you pick, the orchestrator itself
moves from a Copilot Studio "agent" (GPT recognizer + topics + connectors) to
a **Foundry Agent Service agent** — a first-class Azure resource with its own
`agent_id`, instructions, and a list of connected **tools**.

### A.1 Recommended shape: single orchestrator agent + 3 tool categories

```
Foundry Project: "AutoFNOLTriage"
└── Agent: "auto-fnol-triage-orchestrator"
    instructions: (ported almost verbatim from agent.mcs.yml's `instructions:` block)
    tools:
      1. Fabric IQ tool       -> OpenAPI/Action tool or MCP tool hitting the same
                                  Fabric ontology GraphQL/data-agent endpoint
                                  the Copilot Studio "InvokeAutoFNOLOntologyAgent"
                                  action already calls
      2. Foundry IQ tool      -> "Connected agent" tool: the existing Foundry
                                  knowledge agent (Auto-FNOL-Knowledge-Agent),
                                  called directly agent-to-agent within the
                                  same Foundry project (no external hop at all,
                                  since both agents now live in Foundry) -
                                  this is actually SIMPLER than today's setup,
                                  and sidesteps the confirmed Copilot Studio
                                  "Connect to Foundry agent via external
                                  channel" bug entirely.
      3. Work IQ tool(s)      -> two options, see A.2 below
```

Why this is a clean 1:1 port:
- The **instructions** text (routing logic, CAT bulletin clause, SIU routing
  clause) is largely orchestration prose already written for a tool-calling
  LLM — it ports to a Foundry Agent's `instructions` field with only minor
  edits (removing Copilot-Studio-specific phrasing like "the Post Teams
  message action").
- The **Fabric IQ** and **Foundry IQ** tools are unchanged services; only the
  *caller* changes.
- Foundry Agent Service natively supports **connected/sub-agents** (agent
  handoff/tool-calling between agents in the same project), so Foundry IQ's
  knowledge agent can be wired in as a tool with a few lines of Python/SDK
  code (`foundry/create_orchestrator_agent.py` in this repo shows the
  pattern) — no Logic App or middleware needed for that hop.

### A.2 Work IQ tool options (M365 Copilot search from a Foundry agent)

Work IQ isn't a Foundry-native concept — it's M365 Copilot's semantic index
over SharePoint/OneDrive/Outlook. Two ways to expose it to a Foundry agent:

**Option A.2-a — Microsoft 365 Copilot Retrieval API (recommended, when available in your tenant)**
- Microsoft Graph now exposes a **Copilot Retrieval API**
  (`/copilot/retrieval`) that lets any app (with the right Graph permissions
  and user/app context) query the same semantic index Work IQ uses in M365
  Copilot chat — the closest same-index equivalent available outside
  Copilot Studio/M365 Copilot chat itself.
- Wire this as a **Foundry Agent OpenAPI tool** (or a small Azure Function
  wrapping the Graph call, exposed as an Action) that the orchestrator agent
  calls when the CAT-bulletin or SIU-routing conditions in the instructions
  are met.
- Pros: truest like-for-like replacement of "Work IQ Copilot MCP" / "Work IQ
  Mail MCP" connectors; permission-trimmed automatically per the calling
  user/app identity.
- Cons: API is in preview in some tenants/licenses — confirm availability
  before committing to this path for a production accelerator.

**Option A.2-b — Direct Microsoft Graph Search API + Mail API (fallback, always available)**
- Skip the semantic/Copilot-specific index and call **Graph Search API**
  (`/search/query`, scoped to SharePoint sites/drives) for document search,
  and **Graph `/me/messages` or `/users/{id}/messages` with `$search`** for
  mail search.
- This is not the *exact* Work IQ semantic experience (no cross-workload
  Copilot ranking/citations model), but it reuses the same underlying data
  (SharePoint site, Outlook mailbox) and is guaranteed to work in any tenant
  with standard Graph app permissions (`Sites.Read.All`, `Mail.Read`), no
  preview API dependency.
- `foundry/tools_workiq_graph.py` in this repo implements this option as the
  default, with a clearly marked stub to swap in the Retrieval API (A.2-a)
  when available.

### A.3 Multi-agent alternative (optional, for larger solutions)

Instead of one orchestrator agent with 3 tool types, Foundry also supports a
**connected-agent / agent-of-agents** topology:
```
Orchestrator agent (routing + Teams posting only)
 ├── connected agent: Fabric IQ data agent
 ├── connected agent: Foundry IQ knowledge agent   (already exists today)
 └── connected agent: Work IQ retrieval agent       (thin wrapper agent whose
                                                       only tool is the Graph
                                                       call from A.2)
```
This is useful if you want independent versioning/ownership of each tool as
its own agent (e.g., a separate team owns the Work IQ retrieval agent), but
functionally equivalent to A.1 for this accelerator's scope. Recommendation:
**start with A.1** (single orchestrator, simpler to operate) and only split
into A.3 if/when you need independent lifecycle management per tool.

---

## B. Replacing the Power Automate email trigger

Power Automate today does three things: (1) trigger on new email via
`Office 365 Outlook` connector, (2) invoke the Copilot Studio agent via
`ExecuteCopilotAsyncV2`, (3) post the result to Teams. All three responsibilities
move out of Power Automate; two viable replacements:

### B.1 Recommended: Azure Logic App (Consumption or Standard)

- Trigger: **Office 365 Outlook connector — "When a new email arrives (V3)"**
  (the *exact same* managed connector Power Automate uses — Logic Apps and
  Power Automate share the same connector catalog, so this is a drop-in
  trigger swap, not a rewrite).
- Action 1: **HTTP action** calling the Foundry Agent Service REST API
  directly:
  1. `POST /threads` (create a thread for this email/case)
  2. `POST /threads/{id}/messages` (post the email subject/body as the user message)
  3. `POST /threads/{id}/runs` (start a run with the orchestrator `agent_id`)
  4. Poll `GET /threads/{id}/runs/{run_id}` until `status == completed`
  5. `GET /threads/{id}/messages` to read the agent's structured response
- Action 2: **HTTP action (Graph `chatMessage` POST)** or a small **Azure
  Function**/**Logic App custom connector** to post to the correct Teams
  channel/thread (see `docs/teams-concurrency-design.md` for exactly how to
  pick/create the right thread per case).
- Why Logic Apps over Power Automate: **same trigger connector** (zero
  relearning cost for the Outlook trigger), but the workflow **definition is
  infrastructure-as-code** (ARM/Bicep-deployable `workflow.json`), runs in
  **Azure** (not the Power Platform/Dataverse licensing model), and gives
  direct HTTP actions for calling Foundry's REST API without any Copilot
  Studio dependency. See `triggers/logicapp/workflow.json` for a full
  reference implementation.
- Licensing note: Logic Apps Consumption billing is pay-per-execution/action
  (Azure billing), decoupled from Power Platform per-agent/per-message
  licensing — worth comparing costs for your expected email volume.

### B.2 Alternative: Azure Function + Microsoft Graph change notifications (webhook)

- Instead of a polling/managed connector trigger, subscribe to **Microsoft
  Graph change notifications** (`/subscriptions` on the mailbox's `/messages`
  resource) which POSTs a webhook to an **Azure Function (HTTP trigger)**
  whenever a new email arrives — no Outlook *connector* dependency at all
  (pure Graph API + Function).
- The Function then performs the same 3 Foundry REST calls as B.1 Action 1,
  and the same Teams-posting logic as B.1 Action 2, all in code (Python/C#/
  Node) instead of low-code connectors.
- Pros: fully code-first, no connector quota/throttling concerns, easiest to
  unit test, most portable (could run outside Azure Logic Apps entirely,
  e.g., in a container).
- Cons: you own the Graph subscription **renewal** logic (subscriptions
  expire after ~3 days for mail and must be renewed via a timer), and you
  own retry/error-handling code that Logic Apps/Power Automate give you for
  free out of the box.
- `triggers/azure-function/EmailTriggerFunction/` in this repo shows the
  skeleton for this option (HTTP-triggered Function ready to be wired to a
  Graph subscription + a companion renewal timer Function, not fully
  implemented — provided as a starting point since B.1 is the recommended
  default).

### Recommendation

**Use B.1 (Logic App)** for the default accelerator path — it minimizes
migration risk (same trigger connector, same "if it looks like Power
Automate, half your team already knows it" ergonomics) while still fully
removing Copilot Studio and Power Automate specifically as products from the
solution. Reserve B.2 (Function + Graph webhook) for teams that want a
fully code-first, connector-free pipeline and are comfortable owning
subscription renewal.

---

## Summary decision matrix

| Requirement | A.1 (single Foundry agent) | A.3 (multi-agent) | B.1 (Logic App) | B.2 (Function+webhook) |
|---|---|---|---|---|
| Removes Copilot Studio | Yes | Yes | n/a | n/a |
| Removes Power Automate | n/a | n/a | Yes | Yes |
| Reuses Fabric IQ/Foundry IQ/Work IQ unchanged | Yes | Yes | Yes | Yes |
| Lowest migration effort | Yes | No (more moving parts) | Yes (same connector) | No (new subscription/renewal code) |
| Best for independent tool ownership | No | Yes | — | — |
| Fully code-first / IaC-friendly | Yes | Yes | Yes (workflow.json is IaC) | Yes (most code-first) |

**Recommended combination for this accelerator: A.1 + B.1.**
