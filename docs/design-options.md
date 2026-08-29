# Design Options — Orchestrator & Trigger Architecture

Goal: implement Auto FNOL Triage using **Fabric IQ**, **Foundry IQ**, and
**Work IQ** as the three tool/knowledge building blocks, with a
**Foundry Agent Service** orchestrator and an **Azure-native** email-trigger
and notification layer.

This is broken into two independent decisions:

- **A. Where does the orchestrator live and how is it invoked?**
- **B. What triggers the pipeline on new email and posts results to Teams?**

Pick one option from each section; they compose independently.

---

## A. Orchestrator: Azure AI Foundry Agent Service

The orchestrator is a **Foundry Agent Service agent** — a first-class Azure
resource with its own `agent_id`, instructions, and a list of connected
**tools**.

### A.1 Recommended shape: single orchestrator agent + 3 tool categories

```
Foundry Project: "sbazureaimodels-project01"
└── Agent: "auto-fnol-triage-orchestrator"
    instructions: triage routing logic, CAT-bulletin clause, SIU-routing clause
    tools:
      1. Fabric IQ tool       -> MCP tool hitting the Fabric ontology data-agent's
                                  MCP endpoint (AutoFNOL_Ontology)
      2. Foundry IQ tool      -> "Connected agent" tool: the governed knowledge
                                  agent, called directly agent-to-agent within the
                                  same Foundry project
      3. Work IQ tool(s)      -> two options, see A.2 below
```

Why this composition works well:
- The **instructions** are orchestration prose written for a tool-calling
  LLM, covering: routing structured-data questions to Fabric IQ, routing
  general-knowledge questions to Foundry IQ, fraud/subrogation red-flag
  assessment logic, CAT-bulletin and SIU-routing-change lookups via Work IQ,
  and how to summarize outcomes for the calling trigger to post to Teams.
- The **Fabric IQ** and **Foundry IQ** tools are pre-existing governed
  services; the orchestrator only needs to be wired to call them.
- Foundry Agent Service natively supports **connected/sub-agents** (agent
  handoff/tool-calling between agents in the same project), so the Foundry
  IQ knowledge agent can be wired in as a tool with a few lines of Python/SDK
  code (`foundry/create_orchestrator_agent.py` in this repo shows the
  pattern) — no separate middleware needed for that hop.
- The **Fabric IQ** tool is wired using Foundry's native **MCP tool type**
  (`MCPTool` in the `azure-ai-projects` SDK), pointed directly at the Fabric
  ontology data agent's MCP endpoint, authenticated via a project connection
  using `user-entra-token` auth against the
  `https://analysis.windows.net/powerbi/api` audience (the standard pattern
  for Fabric MCP endpoints) — no custom OpenAPI wrapper is required for this
  tool.

### A.2 Work IQ tool options (SharePoint/Outlook search from a Foundry agent)

Two ways to expose SharePoint/OneDrive/Outlook search to a Foundry agent:

**Option A.2-a — Microsoft 365 Copilot Retrieval API (recommended, when available in your tenant)**
- Microsoft Graph exposes a **Copilot Retrieval API** (`/copilot/retrieval`)
  that lets an app (with the right Graph permissions and user/app context)
  query the same semantic index used by M365 Copilot chat.
- Wire this as a **Foundry Agent OpenAPI tool** (or a small Azure Function
  wrapping the Graph call, exposed as an Action) that the orchestrator agent
  calls when the CAT-bulletin or SIU-routing conditions in the instructions
  are met.
- Pros: closest semantic-index parity; permission-trimmed automatically per
  the calling user/app identity.
- Cons: API is in preview in some tenants/licenses — confirm availability
  before committing to this path.

**Option A.2-b — Direct Microsoft Graph Search API + Mail API (fallback, always available)**
- Call **Graph Search API** (`/search/query`, scoped to SharePoint
  sites/drives) for document search, and **Graph `/users/{id}/messages`
  with `$search`** for mail search.
- This is not the *exact* semantic Copilot-ranking experience, but it works
  against the same underlying data (SharePoint site, Outlook mailbox) with
  standard Graph app permissions (`Sites.Read.All`, `Mail.Read`), no preview
  API dependency.
- `foundry/tools_workiq_graph.py` in this repo implements this option as the
  default, with a clearly marked stub to swap in the Retrieval API (A.2-a)
  when available.

### A.3 Multi-agent alternative (optional, for larger solutions)

Instead of one orchestrator agent with 3 tool types, Foundry also supports a
**connected-agent / agent-of-agents** topology:
```
Orchestrator agent (routing + summary only)
 ├── connected agent: Fabric IQ data agent
 ├── connected agent: Foundry IQ knowledge agent
 └── connected agent: Work IQ retrieval agent   (thin wrapper agent whose
                                                   only tool is the Graph
                                                   call from A.2)
```
This is useful if you want independent versioning/ownership of each tool as
its own agent (e.g., a separate team owns the Work IQ retrieval agent), but
functionally equivalent to A.1 for this accelerator's scope. Recommendation:
**start with A.1** (single orchestrator, simpler to operate) and only split
into A.3 if/when you need independent lifecycle management per tool.

---

## B. Email trigger + Teams notification layer

The pipeline needs to (1) trigger on new email, (2) invoke the orchestrator
agent, (3) post the result to Teams. Two viable implementations:

### B.1 Recommended: Azure Logic App (Consumption or Standard)

- Trigger: **Office 365 Outlook connector — "When a new email arrives (V3)"**.
- Action 1: **HTTP action** calling the Foundry Agent Service REST API
  directly:
  1. `POST /threads` (create a thread for this email/case)
  2. `POST /threads/{id}/messages` (post the email subject/body as the user message)
  3. `POST /threads/{id}/runs` (start a run with the orchestrator `agent_id`)
  4. Poll `GET /threads/{id}/runs/{run_id}` until `status == completed`
  5. `GET /threads/{id}/messages` to read the agent's structured response
- Action 2: **HTTP action (Graph `chatMessage` POST)** to post to the
  correct Teams channel/thread (see `docs/teams-concurrency-design.md` for
  exactly how to pick/create the right thread per case).
- Why Logic Apps: the workflow **definition is infrastructure-as-code**
  (ARM/Bicep-deployable `workflow.json`), runs in Azure, and gives direct
  HTTP actions for calling Foundry's REST API. See
  `triggers/logicapp/workflow.json` for a full reference implementation.
- Licensing note: Logic Apps Consumption billing is pay-per-execution/action
  (Azure billing) — worth comparing costs for your expected email volume.

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
  own retry/error-handling code that Logic Apps gives you for free out of
  the box.
- `triggers/azure-function/EmailTriggerFunction/` in this repo shows the
  skeleton for this option (HTTP-triggered Function ready to be wired to a
  Graph subscription + a companion renewal timer Function, not fully
  implemented — provided as a starting point since B.1 is the recommended
  default).

### Recommendation

**Use B.1 (Logic App)** as the default trigger path — lowest operational
overhead (managed connector handles polling/retries), and the workflow
definition is still fully IaC-deployable. Reserve B.2 (Function + Graph
webhook) for teams that want a fully code-first, connector-free pipeline and
are comfortable owning subscription renewal.

---

## Summary decision matrix

| Requirement | A.1 (single Foundry agent) | A.3 (multi-agent) | B.1 (Logic App) | B.2 (Function+webhook) |
|---|---|---|---|---|
| Reuses Fabric IQ/Foundry IQ/Work IQ unchanged | Yes | Yes | Yes | Yes |
| Lowest setup effort | Yes | No (more moving parts) | Yes (managed connector) | No (new subscription/renewal code) |
| Best for independent tool ownership | No | Yes | — | — |
| Fully code-first / IaC-friendly | Yes | Yes | Yes (workflow.json is IaC) | Yes (most code-first) |

**Recommended combination for this accelerator: A.1 + B.1.**
