---
name: fsi-iq-accelerator
description: |
  Builds Microsoft IQ solution accelerators for Financial Services use cases (insurance, banking,
  capital markets, wealth) on the Fabric IQ + Foundry IQ + Work IQ + Copilot Studio orchestrator
  pattern. Carries the three-IQ routing framework, the domain entity model shape, the repo scaffold,
  the deployment sequence with verification gates, and the integration failure modes that are not in
  product documentation. Use when the user asks to "build an FSI accelerator", "create a Microsoft IQ
  accelerator", "design a Fabric IQ ontology for this domain", "add a use case to the accelerator
  pattern", "what belongs in Fabric IQ vs Foundry IQ", or "port the FNOL accelerator to banking".
  Do NOT use for general Fabric migration, capacity planning, Power BI report work, or standalone
  Copilot Studio agents with no data grounding.
license: MIT
metadata:
  category: automation
  icon: BuildingBank
---

# FSI Microsoft IQ Accelerator

Build a new industry accelerator on the proven Auto FNOL Triage pattern: a governed entity graph,
a versioned knowledge base, live workplace signal, and a Copilot Studio agent that routes across
all three. Reference implementation: `sagarbathe/Ins_FNOL_MicrosoftIQ_accelerator`.

## When NOT to Use

- General Fabric platform work: migration, capacity sizing, workspace design, Power BI modelling
- A Copilot Studio agent with no structured data grounding (no ontology, no knowledge base)
- Pure RAG projects where there is no entity graph and no record lookup
- Customer-facing pitch decks about Fabric or Microsoft IQ (that is a slides task)

## The architecture pattern

Four layers. Every accelerator built on this pattern has all four.

| Layer | Role | Serves |
|---|---|---|
| **Fabric IQ** | Ontology over a lakehouse, exposed as a Fabric Data Agent via MCP | Record lookups by ID, relationship traversal |
| **Foundry IQ** | Markdown knowledge docs indexed into Azure AI Search, wrapped in a Foundry Agent | Governed rules, policy wording, playbooks, regulation |
| **Work IQ** | M365 Copilot search and Outlook Mail MCP tools | Live, unindexed, time-sensitive operational signal |
| **Copilot Studio** | The orchestrator agent, surfaced in Teams and M365 Copilot | Routing, combination, conversation |

Plus an optional **unattended intake trigger**: a Power Automate flow that watches a mailbox or
queue and calls the agent to screen inbound items before a human opens them.

## The three-IQ routing framework (the core judgment)

This is the part that generalizes. Apply it before writing any code.

**Fabric IQ when the question names a specific record.** A policy number, an account number, a trade
ID, a customer ID. The answer is a fact in the graph, and getting it wrong is a data error.

**Foundry IQ when the question is about a rule, a concept, or a process.** Coverage wording,
escalation thresholds, regulatory requirement, methodology. The answer is in a document that
someone owns, reviews, and versions.

**Work IQ when the answer changes faster than the knowledge base is republished.** This is the
distinction most people miss, and it is where the pattern earns its keep.

> Foundry IQ is governed and versioned knowledge. Work IQ is live operational drift.

The FNOL reference makes this concrete with two scenarios worth reusing as a template:
1. A named catastrophe event triggers a Work IQ search for an interim bulletin, because the bulletin
   exists in someone's SharePoint days before it reaches the governed knowledge base.
2. A question about case routing or team ownership triggers a Work IQ mail search, because
   operational assignments change more often than the playbook is updated.

**Compound questions use both.** Instruct the orchestrator to resolve the record first, then apply
the rule to it.

### The test to apply per fact
Ask: *who owns this, and how often does it change?*
- Owned by a system of record, changes per transaction, queried by key: **Fabric IQ**
- Owned by a named team, versioned, reviewed on a cycle: **Foundry IQ**
- Owned by whoever sent the email, changes ad hoc, never formally published: **Work IQ**

## Domain entity model shape

Every FSI use case in this pattern resolves to the same five slots. Fill them before writing
`create_ontology.py`.

| Slot | Purpose | Insurance example | Banking example | Capital Markets example |
|---|---|---|---|---|
| **Core record** | The thing being triaged | Claim | Alert / Dispute | Trade |
| **Party** | Who it belongs to | Policyholder | Customer | Counterparty |
| **Contract** | The governing agreement | Policy | Account | Instrument / Agreement |
| **Asset or event detail** | What it concerns | Vehicle | Transaction | Settlement |
| **Risk flags** | Why it needs attention | FraudSignal, SubrogationFlag | TypologyHit, SanctionsScreen | BreakReason, ExceptionType |
| **Assignee** | Who resolves it | Adjuster | Analyst | Ops owner |

Relationships to model explicitly: core record to party, core record to contract, core record to
assignee (easy to forget, and the agent cannot answer "who owns this" without it), risk flags to
core record.

Keep the entity count at or below eight. A larger graph degrades agent reasoning and adds
deployment time without adding demo value.

## Repo scaffold

```
{domain}/
  datagen/      synthetic data generator + lakehouse loader
  fabric/       ontology creation, data agent configure + publish, MCP test
  foundry/      kb_docs/*.md, search index build, agent creation
  copilotstudio/
    {Agent}/            pac copilot pull/push format
    {Agent}_solution/   full Dataverse export (required for Power Automate flows)
  documents/    design doc, prerequisites, sample intake items, deployment guide
  config.py     reads every tenant value from environment
  .env.example
```

**No tenant-specific values in source.** Workspace IDs, lakehouse IDs, endpoints, agent IDs and
mailbox folder IDs all come from environment or documented placeholders. Display names get sensible
defaults so a new user only fills in what is genuinely theirs.

## Build sequence

Run in this order. Each step has a verification gate; do not proceed past a failed gate.

1. **Domain model.** Fill the six slots above. Write the routing rules in prose before building.
   *Gate: a reviewer can state which IQ answers each of ten sample questions.*
2. **Synthetic data.** Generate and load to the lakehouse.
   *Gate: row counts present in all tables, referential integrity holds.*
3. **Fabric IQ.** Create the ontology, configure and publish the Data Agent.
   *Gate: the MCP test script returns a correct record for a known ID.*
4. **Foundry IQ.** Author `kb_docs/*.md`, build the search index, create the Foundry Agent with
   Activity Protocol enabled.
   *Gate: the agent answers a concept question with a citation to the right doc.*
5. **Copilot Studio orchestrator.** Push the agent, wire connection references, publish, enable Teams.
   *Gate: a structured question, a concept question, and a compound question all answer correctly
   in the Test pane AND in Teams (they fail differently).*
6. **Unattended intake flow.** Pack and import the Dataverse solution, reconnect references,
   re-point the mailbox folder.
   *Gate: a live test item produces a Teams notification.*

### If a gate fails

Do not carry a failure forward. Each gate failure has one correct recovery:

- **If the MCP test returns no record**, the ontology published but the data agent did not pick up
  the schema. Re-publish the data agent before touching the ontology.
- **If the Foundry agent answers without citations**, the index built empty or the wrong container
  was indexed. Re-run the index build and confirm document count before re-testing.
- **If a question answers in Test chat but errors in Teams**, do not debug the agent logic. Check
  the channel and the connection references first.
- **If the same script fails twice with the same error**, stop and report the blocker rather than
  retrying a third time or working around it silently.
- **If a required tenant value is missing**, halt and ask for it. Never substitute a plausible
  workspace ID, endpoint, or resource name.

## Integration failure modes

Symptom first, because that is how someone finds this when they are stuck.

**Symptom: `Parse_JSON` returns null content in the unattended flow, no useful error.**
The flow ran without an interactively signed-in user, so an interactive-auth-gated tool (Foundry
knowledge, Work IQ) could not complete. Unattended screening must work from the item text alone,
with zero tool calls. Design the intake prompt to reason purely over the narrative.

**Symptom: Power Automate flow is missing after `pac copilot pull`.**
Flows are not reachable via `pac copilot pull/push`. They require full Dataverse solution
export/unpack/pack/import. This is why the scaffold has two differently shaped Copilot Studio
folders. Do not try to unify them.

**Symptom: Copilot Studio or Teams cannot call the Foundry agent.**
The Foundry agent needs Activity Protocol plus the BotServiceRbac endpoint configuration. Enable it
in the agent creation script so it is never a manual afterthought.

**Symptom: the intake trigger fires on the wrong folder, or not at all.**
Mailbox folder IDs are per-mailbox and cannot be templated or shared across tenants. Import with
placeholder text, then re-point the folder in the Power Automate designer so it populates real IDs.

**Symptom: everything worked yesterday, now connections fail.**
Connection references break after `pac copilot pull`. Reconnect them before assuming a real defect.

**Symptom: 429 throttling during index build or data agent calls.**
Expected on shared capacity. Add backoff to the scripts rather than reducing scope.

**Symptom: works in Copilot Studio Test chat, fails in Teams.**
Test chat and the Teams channel authenticate differently. Always validate in both. Work IQ tools in
particular require a signed-in user.

## Guardrails

- **Do not generalize from one instance.** If only one accelerator exists in this pattern, build the
  second one semi-manually and record every change required. The delta is the real pattern.
- **Never invent regulatory content.** Knowledge base docs about regulation, compliance thresholds or
  supervisory expectations must be sourced or clearly marked as illustrative synthetic content. An
  accelerator demo that states a wrong regulatory threshold to a customer is a serious problem.
- **Synthetic data only.** Never seed an accelerator with real customer records, even anonymized.
- **State availability honestly.** Several components in this pattern are preview. Flag preview
  status in customer-facing material rather than implying general availability.
- **No tenant values in the repo.** Check before every commit.
