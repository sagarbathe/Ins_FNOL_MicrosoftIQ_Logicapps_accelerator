---
name: fsi-iq-logicapps-accelerator
description: |
  Builds Microsoft IQ solution accelerators for Financial Services (insurance, banking, capital
  markets, wealth) where Azure Logic Apps agent loops are the orchestrator instead of Copilot Studio.
  Carries the autonomous-vs-conversational choice, the three tooling layers (connectors, custom
  connectors, MCP servers), agent-parameter scoping rules, allowed-tools discipline, and the preview
  constraints that break Logic Apps plus Foundry integrations. Use when the user asks to "build an
  FSI accelerator with Logic Apps", "use an agent loop instead of Copilot Studio", "expose a Logic
  App as an MCP server for Foundry", "add a use case to the Logic Apps accelerator", or "port the
  FNOL Logic Apps accelerator to banking". Do NOT use for the Copilot Studio variant of this pattern,
  for non-agentic Logic Apps integration work, or for general Fabric or Power BI projects.
license: MIT
metadata:
  category: automation
  icon: Flow
---

# FSI Microsoft IQ Accelerator (Logic Apps orchestrator)

The sibling of the Copilot Studio accelerator pattern. Same grounding layers, different orchestrator:
an **Azure Logic Apps agent loop** replaces the Copilot Studio agent. Reference implementation:
`sagarbathe/Ins_FNOL_MicrosoftIQ_Logicapps_accelerator`.

## When NOT to Use

- The **Copilot Studio** variant of this pattern (use the Copilot Studio accelerator skill instead)
- Non-agentic Logic Apps work: plain integration workflows, connectors, B2B, EDI
- General Fabric platform work, capacity sizing, or Power BI modelling
- A Foundry agent with no Logic Apps involvement

## Why Logic Apps instead of Copilot Studio

Pick deliberately, and state the reason in the design doc.

| Choose Logic Apps when | Choose Copilot Studio when |
|---|---|
| The entry point is an event, not a person (queue, mailbox, webhook, API) | The entry point is a human in Teams or M365 Copilot |
| You need the 1,400+ connector surface to reach line-of-business systems | Conversation and M365 surfacing are the point |
| You want the orchestration in source control as workflow definitions | You want the maker experience and Dataverse solution lifecycle |
| The customer's integration team already owns Logic Apps | The customer's business team owns the agent |
| You need the orchestrator to also be callable as an MCP tool provider | Work IQ (live M365 signal) is central to the scenario |

**Work IQ is the honest trade.** M365 Copilot search and Outlook Mail tools need an interactively
signed-in user. A Logic Apps agent loop running autonomously does not have one. If live M365 signal
is load-bearing, either keep a Copilot Studio surface for the human path or accept that the
autonomous path runs on Fabric IQ and Foundry IQ only.

## Grounding layers (unchanged from the Copilot Studio variant)

- **Fabric IQ**: ontology over a lakehouse, published as a Fabric Data Agent, reached over MCP.
  Answers questions that name a specific record ID.
- **Foundry IQ**: markdown knowledge docs indexed into Azure AI Search behind a Foundry Agent.
  Answers rule, concept and process questions from governed, versioned content.
- **Work IQ**: live M365 signal for operational drift that has not been republished into the
  knowledge base. Interactive paths only, per the caveat above.

Routing test per fact: *who owns it, and how often does it change?* System of record queried by key
goes to Fabric IQ. Team-owned and versioned goes to Foundry IQ. Ad hoc and never formally published
goes to Work IQ.

## Agent loop shape

A Logic Apps agent loop iterates **Think, Act, and optionally Learn**: it reasons over the inputs,
chooses a tool, acts, and continues until the goal is met. Two patterns:

- **Autonomous** — no human in the loop. This is the intake and triage path: an inbound item
  arrives, the agent screens it, and it posts or routes the outcome.
- **Conversational** — a human interacts turn by turn. This is the analyst-facing path.

Most FSI accelerators need both, as two workflows over the same tools. Build the autonomous one
first: it is the demo that lands, because it shows work happening before anyone opens the item.

Consumption agentic workflows are **preview**; Standard is the safer target for anything a customer
will see. State preview status explicitly in customer material.

## The three tooling layers

Choose the lowest layer that does the job.

**Layer 1: built-in and managed connectors.** A tool is a sequence of one or more connector actions
inside the tool container. Most FSI accelerators need Azure OpenAI, Azure AI Search, Office 365
Outlook, SQL Server, HTTP and Service Bus.

**Layer 2: custom connectors.** An OpenAPI wrapper around any REST API: the policy admin system, the
core banking platform, the order management system. Once registered it behaves like a managed
connector and is reusable tenant-wide, not just in agentic workflows.

**Layer 3: MCP servers.** Two directions, and they are different architectures:
- *Consuming*: the agent loop calls an external MCP server (the Fabric Data Agent, a Foundry
  knowledge agent, a Microsoft-provided server).
- *Providing*: the Logic App itself becomes the MCP server, and the calling agent lives in Foundry.
  This is what lets one set of tools serve several agents, with per-tool run history and independent
  debugging.

**Tool names and descriptions are load-bearing.** The model chooses a tool by reading its name and
description, so these are a design decision, not a label. Write them the way you would write an API
contract: what it does, when to use it, what it needs.

### Example: a tool definition that routes correctly

```yaml
name: lookup_claim_by_id
description: >
  Returns the full claim record for a known claim ID, including policy,
  policyholder, vehicle, assigned adjuster, and any open risk flags.
  Use when the request names a specific claim number. Do NOT use for
  questions about coverage rules or process, which have no claim ID.
parameters:
  claim_id:
    type: string
    description: The claim number, format CLM-nnnnnnn.
backing_action: Fabric Data Agent (MCP) - query ontology
```

Note the three things that make it selectable: the trigger condition stated positively, an explicit
exclusion pointing at the sibling tool, and a parameter description precise enough that the model
can extract the value from a sentence. A description reading "gets claim data" fails all three.

## Build sequence

1. **Domain model.** Fill the entity slots: core record, party, contract, asset or event detail,
   risk flags, assignee. Keep the ontology at eight entities or fewer.
   *Gate: a reviewer can name which grounding layer answers each of ten sample questions.*
2. **Synthetic data**, generated and loaded to the lakehouse.
   *Gate: row counts present, referential integrity holds.*
3. **Fabric IQ**: ontology created, Data Agent configured and published.
   *Gate: an MCP call returns the correct record for a known ID.*
4. **Foundry IQ**: knowledge docs indexed, Foundry Agent created.
   *Gate: a concept question returns an answer citing the right document.*
5. **Tool definition.** Define each tool with its name, description and agent parameters before
   wiring the loop.
   *Gate: read the tool list cold and predict which one the model picks for five sample prompts.*
6. **Autonomous agent loop.** Trigger, agent action, tools, output handling.
   *Gate: a live test item runs end to end and the run history shows the expected tool sequence.*
7. **Conversational workflow** over the same tools, if the scenario needs a human path.
   *Gate: a compound question resolves a record and then applies a rule to it.*

### If a gate fails

- **If the agent picks the wrong tool**, fix the tool description before touching the system prompt.
  Tool selection is description-driven; prompt patches mask the real defect.
- **If the agent never calls a tool**, check that agent parameters are defined on that tool, not
  inherited from elsewhere, and that the description states the trigger condition.
- **If a run succeeds but the output is empty**, read the run history per tool rather than the
  workflow summary. MCP-backed tools fail quietly at the tool boundary.
- **If the same deployment step fails twice identically**, stop and report the blocker. Do not retry
  a third time or route around it silently.
- **If a tenant value is missing**, halt and ask. Never substitute a plausible resource name,
  workspace ID, or endpoint.

## Known constraints and failure modes

Symptom first, because that is how someone finds this when they are stuck.

**Symptom: the agent is slow, expensive, and picks odd tools.** An attached MCP server exposed all
of its tools into the agent's context. Microsoft-provided servers often expose many. **Restrict
allowed tools to only what the agent needs** — find the exact tool name in the run history and lock
it in advanced settings. This is the single highest-value tuning step in the pattern.

**Symptom: a tool's parameter is unset or empty at runtime.** Agent parameters are scoped to the
tool that defines them and cannot be shared across tools. They also receive values only when the
agent invokes the tool, not at workflow start. Define them per tool; do not expect workflow
parameters to flow in.

**Symptom: the Logic Apps tool option is missing in Foundry.** Adding a Logic Apps-backed agent tool
is available only in the **preview** Foundry portal, not the classic portal.

**Symptom: you cannot add the second connector to a tool.** In the Foundry-side flow you can select
only **one** connector per tool, and after selection you cannot change or delete it. Plan the tool
boundary before you start the wizard, and build a new tool rather than trying to edit.

**Symptom: the connector you need is not selectable.** That release supports only managed connectors
that do **not** use OAuth 2.0 authentication. An OAuth-based line-of-business connector needs a
different path: a custom connector, or an HTTP action with your own auth.

**Symptom: the selected connector shows no available actions.** A known Azure portal issue. Pick a
different connector or reach the API via HTTP.

**Symptom: the Azure portal never returns you to Foundry.** Pop-ups are blocked. Allow
`https://portal.azure.com:443`.

**Symptom: you cannot create the Foundry resource at all.** Creating it requires the **Owner** role
on the subscription. Sort this before a workshop, not during one.

**Symptom: business logic is duplicated across agents and hard to debug.** Inline tools live inside
the agent loop and do not scale across agents. Move them into a Logic App exposed as an MCP server,
then delete the inline copies.

## Guardrails

- **Do not generalize from one instance.** Build the second domain semi-manually and record every
  change required. The delta is the real pattern; a skill written from a single build over-prescribes
  the accidental and under-specifies the essential.
- **Never invent regulatory content.** Knowledge base documents covering regulation, thresholds or
  supervisory expectations must be sourced or clearly marked illustrative. A wrong threshold stated
  to a customer is a serious problem, not a cosmetic one.
- **Synthetic data only.** Never seed an accelerator with real customer records, even anonymized.
- **State preview status honestly.** Several components here are preview. Say so in customer
  material rather than implying general availability.
- **Autonomous loops take real actions.** Any tool that sends, posts, writes or files must be
  deliberate. Default the autonomous path to notify-only and require an explicit decision before
  giving it a write tool.
- **No tenant values in the repo.** Workspace IDs, endpoints, resource names and mailbox folder IDs
  come from environment or documented placeholders. Check before every commit.
