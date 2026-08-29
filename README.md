# Auto FNOL Triage — Logic Apps / Foundry-Native Accelerator

This is a **sibling solution** to
[`Ins_FNOL_MicrosoftIQ_accelerator`](https://github.com/sagarbathe/Ins_FNOL_MicrosoftIQ_accelerator)
(the Copilot Studio + Power Automate based Auto FNOL Triage solution).

It answers two questions:

1. **Can we deliver the same Auto FNOL Triage capability without Copilot Studio
   and without Power Automate**, while keeping the three "IQ" building blocks
   intact — **Fabric IQ** (ontology/data agent), **Foundry IQ** (governed
   knowledge agent), and **Work IQ** (M365 Copilot / Graph search over
   SharePoint + Outlook)? See [`docs/design-options.md`](docs/design-options.md).

2. **When multiple emails arrive concurrently, each spawning its own Teams
   alert, how do we keep each case's context/thread isolated** so replies and
   follow-ups don't get cross-wired between cases? See
   [`docs/teams-concurrency-design.md`](docs/teams-concurrency-design.md).

This solution does **not** modify or depend on the original Copilot Studio
agents/flows in any way — it is a parallel, independent implementation for
comparison/demo purposes, reusing the **same underlying Fabric IQ ontology,
Foundry IQ knowledge agent/index, and Work IQ (Graph) integration** the
original solution already built.

## What's kept vs. replaced

| Component | Original solution | This solution |
|---|---|---|
| Orchestrator / Triage "agent" | Copilot Studio agent (GPT recognizer + topics) | **Azure AI Foundry Agent Service** agent (single orchestrator agent with connected tools) |
| Policy/Claim data (Fabric IQ) | Fabric ontology MCP tool, invoked from Copilot Studio | **Same** Fabric ontology / GraphQL data agent, invoked as a Foundry Agent **tool** (Action/OpenAPI or MCP tool) |
| Governed knowledge (Foundry IQ) | Foundry knowledge agent, invoked from Copilot Studio as a connector | **Same** Foundry knowledge agent — now invoked as a **Foundry Agent-to-Agent (connected agent) tool**, i.e. the orchestrator agent calls it directly inside the same Foundry project (no cross-product hop) |
| Fresh/ungoverned knowledge (Work IQ) | Work IQ Copilot MCP + Work IQ Mail MCP connectors in Copilot Studio | **Same** Work IQ capability, exposed to the Foundry agent via the **Microsoft Graph / Copilot Retrieval API** tool (see design doc, Option A) or a thin Graph-calling Azure Function tool (Option B) |
| Email trigger | Power Automate `OnNewEmailV3` → `ExecuteCopilotAsyncV2` flow | **Azure Logic App** (Standard or Consumption) `Office 365 Outlook – When a new email arrives (V3)` trigger → calls the Foundry agent's REST **Threads/Runs API** directly (no Copilot Studio, no Power Automate) |
| Teams notification | Power Automate `Post message in a chat/channel` action, single flow-bot identity | **Bot Framework / Microsoft Graph `chatMessage`** post from the Logic App / Function, with a **dedicated Teams thread-per-case** (see concurrency design) — avoids the flow-bot "Bot hasn't been installed" issue entirely for repeat channels since it uses Graph app permissions, not a Teams app install per flow |

## Repo layout

```
docs/
  design-options.md              <- Question 1: orchestrator + trigger replacement options
  teams-concurrency-design.md    <- Question 2: per-case Teams thread isolation design
foundry/
  create_orchestrator_agent.py   <- Provisions the Foundry orchestrator agent + connected tools
  tools_fabric_iq.py              <- Fabric IQ ontology tool definition (OpenAPI/MCP) for the Foundry agent
  tools_workiq_graph.py           <- Work IQ (Graph/Copilot Retrieval API) tool definition
  run_agent_thread.py              <- Helper: create thread, post message, run, poll, read response
triggers/
  logicapp/
    workflow.json                 <- Logic App (Consumption) definition: Outlook trigger -> Foundry Agent run -> Teams post
    connections.json.template
    parameters.json.template
  azure-function/EmailTriggerFunction/
    function.json                 <- Alternative: Event Grid / Graph-webhook-driven Function trigger
    __init__.py
shared/
  case_thread_store_schema.sql    <- Table schema for case-id -> Teams thread mapping (concurrency design)
  post_to_teams.py                <- Shared helper for Graph-based Teams channel/thread posting
```

## Relationship to the original repo

- Fabric IQ setup scripts (`fabric/create_ontology.py`, `configure_data_agent_ontology.py`, etc.) are
  **reused as-is** from the original repo — no changes needed, since Fabric IQ is invoked the same
  way (as a data agent / MCP endpoint) regardless of which orchestrator calls it.
- Foundry IQ knowledge agent (`foundry/create_foundry_agent.py`, `foundry/build_search_index.py`) is
  **reused as-is** — the same Foundry knowledge agent is simply invoked as a **connected agent tool**
  from the new orchestrator agent instead of via a Copilot Studio connector.
- Work IQ sample content (`documents/workiq_samples/*`) is **reused as-is** — the CAT bulletin and
  SIU routing email demo artifacts work identically regardless of orchestrator.