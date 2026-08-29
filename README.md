# Auto FNOL Triage — Foundry-Native, Logic Apps Accelerator

An Azure-native Auto FNOL (First Notice of Loss) Triage solution built entirely on
**Azure AI Foundry Agent Service**, **Azure Logic Apps**, **Microsoft Fabric**, and
**Microsoft Graph** — no low-code conversational-AI authoring surface required for
the orchestration layer.

It answers two core design questions for this solution:

1. **Architecture**: how the orchestrator, Fabric IQ data agent, Foundry IQ
   knowledge agent, and Work IQ (Graph/SharePoint/Outlook) search tool are
   composed into a single triage pipeline. See
   [`docs/design-options.md`](docs/design-options.md).

2. **Concurrency & context isolation**: when multiple emails arrive at once,
   each spawning its own Teams alert, how each case's conversation stays
   isolated so replies and follow-ups never get cross-wired between cases.
   See [`docs/teams-concurrency-design.md`](docs/teams-concurrency-design.md).

## Architecture at a glance

| Component | Role |
|---|---|
| **Orchestrator agent** | A single **Azure AI Foundry Agent Service** agent (`auto-fnol-triage-orchestrator`) — owns triage instructions/routing logic and calls the three tools below. |
| **Fabric IQ** | A Microsoft Fabric ontology data agent (`AutoFNOL_Ontology`, exposed via Fabric's MCP endpoint) providing structured Policy/Claim/Policyholder/Vehicle/Adjuster/RepairShop/FraudSignal/SubrogationFlag data. Called as an **MCP tool** from the orchestrator agent. |
| **Foundry IQ** | A governed knowledge agent (policy wording, coverage rules, FNOL triage tiers, SIU fraud red flags, state regulatory requirements, subrogation methodology) backed by an Azure AI Search index with citations. Called as a **connected agent tool** from the orchestrator agent — both agents live in the same Foundry project, so this is a direct agent-to-agent call. |
| **Work IQ** | Fresh/ungoverned operational content (CAT bulletins, routing-change emails) discovered via Microsoft Graph Search API (SharePoint/OneDrive) and Graph mail search (Outlook), exposed to the orchestrator as an **OpenAPI/Action tool**. |
| **Email trigger** | An **Azure Logic App** with the Office 365 Outlook "When a new email arrives (V3)" trigger, calling the orchestrator agent's Threads/Runs REST API and posting the result to Teams. An **Azure Function + Graph webhook** alternative is also provided for a fully code-first pipeline. |
| **Case/Teams correlation store** | A **Fabric SQL Database** table (`CaseThreadMap`) mapping each case to its Teams reply-chain root message and its Foundry thread, so concurrent cases never get mixed up (see the concurrency design doc). |

## Repo layout

```
docs/
  design-options.md              <- Architecture: orchestrator + tool composition + trigger options
  teams-concurrency-design.md    <- Per-case Teams thread isolation design
foundry/
  create_orchestrator_agent.py   <- Provisions the Foundry orchestrator agent + connected tools
  tools_fabric_iq.py              <- Fabric IQ ontology MCP tool wiring
  tools_workiq_graph.py           <- Work IQ (Graph Search/Mail) tool implementation
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
  case_thread_store_schema.sql    <- Fabric SQL Database schema: case-id -> Teams thread mapping (concurrency design)
  post_to_teams.py                <- Shared helper for Graph-based Teams channel/thread posting
```


## Relationship to the underlying IQ components

- **Fabric IQ**: this solution consumes the `AutoFNOL_Ontology` Fabric data
  agent as-is via its MCP endpoint; no changes to the ontology, graph model,
  or data-agent configuration are required.
- **Foundry IQ**: this solution consumes the existing governed knowledge
  agent as a connected agent tool within the same Foundry project; no
  changes to the knowledge agent or its Azure AI Search index are required.
- **Work IQ**: this solution reimplements the same document/mail-search
  capability directly against Microsoft Graph (Search API + Mail API), so no
  separate low-code connector is required.