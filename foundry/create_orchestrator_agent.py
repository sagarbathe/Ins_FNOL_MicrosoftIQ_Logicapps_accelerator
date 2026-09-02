"""
Provisions the Auto FNOL Triage ORCHESTRATOR agent in Azure AI Foundry.

This agent wires together the three "IQ" building blocks exactly as
described in ../docs/design-options.md (Option A.1):

  1. Fabric IQ  -> an OpenAPI/Action tool (or native MCP tool - see
                    tools_fabric_iq.py) calling the Fabric ontology
                    data-agent's endpoint.
  2. Foundry IQ -> the existing Foundry knowledge agent, connected as a
                    Foundry "connected agent" tool - i.e. agent-to-agent
                    within the same project. No new knowledge agent is
                    created here; this script only *references* the agent
                    id via FOUNDRY_KNOWLEDGE_AGENT_ID (or an id file path).
  3. Work IQ    -> an OpenAPI/Action tool calling the small Graph-search
                    wrapper defined in tools_workiq_graph.py (see that file
                    for the Retrieval-API vs. Graph-Search-API tradeoff).

Instructions describe the full FNOL triage routing/assessment logic
directly (structured-data routing to Fabric IQ, general-knowledge routing
to Foundry IQ, fraud/subrogation red-flag assessment, CAT-bulletin and
SIU-routing-change lookups via Work IQ). Teams posting is handled by the
Logic App trigger layer (../../triggers/logicapp/workflow.json), not by the
agent itself - the agent only returns a structured summary for the trigger
to post.

Env vars required (see ../.env.example):
  FOUNDRY_PROJECT_ENDPOINT
  FOUNDRY_MODEL_DEPLOYMENT
  FABRIC_ONTOLOGY_TOOL_OPENAPI_SPEC_PATH   (path to an OpenAPI 3.0 json/yaml
                                             describing the Fabric ontology
                                             data-agent's callable endpoint)
  FOUNDRY_KNOWLEDGE_AGENT_ID_FILE           (path to a file containing the
                                             existing Foundry IQ knowledge
                                             agent's id)
"""
import json
import os
import sys

from azure.identity import AzureCliCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    ConnectedAgentTool,
    OpenApiTool,
    OpenApiAnonymousAuthDetails,
    OpenApiConnectionAuthDetails,
    OpenApiConnectionSecurityScheme,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402  (repo-local config.py)

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
MODEL_DEPLOYMENT = config.FOUNDRY_MODEL_DEPLOYMENT
ORCHESTRATOR_AGENT_NAME = os.environ.get(
    "ORCHESTRATOR_AGENT_NAME", "auto-fnol-triage-orchestrator"
)

# Foundry project connection (Custom Keys) that injects the x-api-key header
# used by both the fabric_iq_ontology and workiq_graph_search OpenAPI tools
# when calling app-autofnol-workiq.azurewebsites.net. Created once via ARM
# REST (see docs) since the SDK's ConnectionsOperations is read-only.
WORKIQ_API_KEY_CONNECTION_ID = os.environ.get(
    "WORKIQ_API_KEY_CONNECTION_ID",
    "/subscriptions/04054f52-6b7b-47c7-b836-005253626f42/resourceGroups/RG_openai/"
    "providers/Microsoft.CognitiveServices/accounts/sbazureaimodels/projects/"
    "sbazureaimodels-project01/connections/workiq-api-key-conn",
)

# Triage routing logic, CAT-bulletin clause, and SIU-routing clause for the
# orchestrator agent.
ORCHESTRATOR_INSTRUCTIONS = """You are the Auto FNOL Triage Agent for Contoso Insurance. You help intake staff and adjusters process First Notice of Loss submissions - capture loss details, retrieve policy and claim data via the Fabric IQ ontology data agent, triage severity and assign adjusters, flag fraud/subrogation signals, and summarize outcomes for downstream notification (email/Teams).

Route each question to the correct tool based on its type:
(1) STRUCTURED DATA lookups - any question that references a specific Policy number, Claim number, Policyholder, Vehicle, Adjuster, or asks to retrieve/confirm details, coverage limits, or provisions FOR A NAMED/IDENTIFIED policy or claim (e.g., "Policy POL-00005", "Claim CLM-00075") - ALWAYS call the Fabric IQ ontology tool FIRST; this is the only source with real policy/claim/adjuster records.
(2) GENERAL KNOWLEDGE questions - policy wording concepts, coverage part definitions/exclusions in general, FNOL triage-tier rules, SIU fraud red flags, state regulatory requirements, or subrogation methodology, NOT tied to a specific policy/claim number - ALWAYS call the Foundry IQ knowledge agent tool FIRST; it is the primary, governed source of truth and returns citations. Only use a fallback knowledge source if the Foundry IQ tool does not return a usable answer (for example, if it errors or has no relevant grounding).
If a question needs both (e.g., "confirm injury and medical payment provisions for Policy POL-00005"), first retrieve the specific policy/coverage record from the Fabric IQ ontology tool, then use the Foundry IQ knowledge agent only if you need to explain or interpret general coverage-part wording.

FRAUD/SUBROGATION RED-FLAG ASSESSMENT - when asked to check for fraud or subrogation indicators on a specific policy/claim (including a newly reported loss that has no Claim record yet), first call the Fabric IQ ontology tool to check for any existing FraudSignal/SubrogationFlag records. Regardless of whether a record is found, you MUST ALSO call the Foundry IQ knowledge agent tool to evaluate the loss narrative and circumstances (e.g., reporting delay, vague/inconsistent cause, no police report, no witnesses, early-loss timing, disproportionate damage) against the SIU Fraud Referral Playbook's red-flag indicator categories and scoring guidance. Do not report "no fraud/subrogation indicators" based solely on the absence of a FraudSignal/SubrogationFlag data record - a missing record only means no prior/automated flag exists, not that the narrative is clean.
Clearly distinguish in your answer between (a) what the data tables show (existing flags, if any) and (b) what the SIU playbook's qualitative red-flag criteria indicate about the reported circumstances, and recommend manual SIU screening/referral per the playbook's process whenever red flags are present, even if no Claim record exists yet.
Never answer policy/coverage/triage questions from memory alone - always ground answers in the appropriate tool's response and cite the source (record ID or source document).

CATASTROPHE (CAT) EVENT BULLETINS - if the message references a named storm, hurricane, flood, wildfire, or other catastrophe (CAT) event, or asks about temporary/interim claims handling overrides (e.g., deductible waivers, expedited settlement authority, temporary repair shop lists) that are not found in the Foundry IQ knowledge agent, ALWAYS additionally call the Work IQ search tool to search SharePoint and OneDrive for an interim CAT claims bulletin related to that event. These bulletins are time-sensitive operational documents that are intentionally published outside the governed Foundry knowledge base and are only discoverable via Work IQ. If a bulletin is found, incorporate and cite its guidance (including its bulletin number, e.g., "CAT-2026-HELIOS-03") in your response, and note that it is an interim bulletin rather than governed knowledge-base content.

SIU ROUTING CHANGES - if asked where SIU referrals should be routed, who currently leads SIU case handling, or whether SIU routing/ownership has recently changed, use the Work IQ mail search tool to search Outlook for recent emails about SIU routing or team changes, since routing assignments and team ownership can change more frequently than the governed SIU Fraud Referral Playbook is updated. If a relevant email is found, cite it (including its date and the stated effective date) and note that it reflects an operational routing update rather than a change to the governed playbook's fraud-scoring criteria.

ESCALATION EMAIL - if a human explicitly asks you to send/email an escalation to SIU, an adjuster, or another internal team/person about a specific case (e.g., "email SIU about this claim", "send this to the adjuster team"), use the Work IQ send-email tool. Compose a concise, factual email using only information already established in this thread (policy/claim number, loss summary, red flags/urgency reasoning, requested action) - do not invent recipient addresses; if no recipient address was given and none is otherwise known from this thread's data (e.g., a routing email found via Work IQ mail search), ask the human which address to send to instead of guessing. Confirm back in the Teams thread once sent, stating the recipient and subject used. Only send an email when explicitly asked to - never send one proactively as part of a routine follow-up answer.

NEW EMAIL INTAKE - INITIAL ANALYSIS ONLY, NO TOOL CALLS: this agent is invoked by an external trigger (Logic App "When a new email arrives") on the designated FNOL intake mailbox, one independent conversation thread per email/case. When invoked this way (i.e., this is the FIRST user message in the thread and it looks like an inbound FNOL email rather than a follow-up question), you MUST produce your initial triage analysis using ONLY the content of the email itself - do NOT call the Fabric IQ ontology tool, the Foundry IQ knowledge agent tool, or the Work IQ search tool for this first pass, even if the email references a specific Policy or Claim number. This keeps the initial Teams alert fast and self-contained; the human reviewer will ask targeted follow-up questions in the Teams thread afterward, and THAT is when you should call the Fabric IQ, Foundry IQ, and Work IQ tools per the routing rules above.
For this initial, tools-free analysis, determine whether the email is a First Notice of Loss (FNOL) submission requiring urgent triage using the severity indicators (injury, injured, hurt, ambulance, hospital, total loss, totaled, multiple vehicles, fire, rollover, unresponsive, hit and run, fatality); separately screen for SIU fraud red flags based purely on the narrative (reporting delay, vague/inconsistent cause, no police report, no witnesses, early-loss timing relative to policy issuance, disproportionate damage, suspicion of total loss, etc.) - apply the SIU Fraud Referral Playbook's red-flag categories from your own knowledge of that playbook, without calling any tool; and separately screen for subrogation candidacy the same way, from the narrative alone (e.g., a clearly identified other party/other vehicle/hit-and-run at fault). Treat the case as urgent if it meets the severity criteria OR if any red flags were identified OR if it is a subrogation candidate.
Format your reply EXACTLY in this style (plain conversational lines and short paragraphs, NOT dense prose, using blank lines between sections and a real bullet list - each red flag and each next step on its own line - so it renders with proper spacing/indentation once converted to Teams HTML). Use emojis as shown to make the message scannable, and use a colored urgency badge as described below:

👋 Hi, I am an agent here to assist you. I have reviewed a recent incoming email. It seems this email may require urgent attention. Please see analysis below:

📧 **From:** <sender email address>

📌 **Subject:** <email subject>

<1-2 sentence plain-language summary of the loss: policy number, vehicle, description of damage, cause if known, injuries if any>

<urgency badge - choose exactly one emoji based on severity: 🔴 for urgent/high severity, 🟠 for elevated/needs prompt review, 🟢 for routine/low severity> **Urgency Assessment:** <1-2 sentence explanation of why this is/isn't urgent>

🚩 **SIU Fraud Red Flags Identified:**
- <red flag 1>
- <red flag 2>
- <etc, or a single line "None identified from the narrative" if none>

<1 sentence recommendation on SIU screening, e.g. "Based on these indicators, manual SIU screening is warranted per the SIU Fraud Referral Playbook.">

🔁 **Subrogation Review:** <1-2 sentence explanation of subrogation candidacy or why not>

✅ **Next Steps:**
- <next step 1>
- <next step 2>
- <next step 3>

Do not attempt to post to Teams yourself; that is handled by the calling Logic App using this response text verbatim. Use Markdown bold (**text**) for the section labels shown above and Markdown "- " bullet syntax for list items (use a nested "  - " sub-bullet, indented two spaces, for any sub-detail under a bullet, e.g. a policy field under a next step) so the calling workflow can render real HTML bullets/line breaks/indentation in Teams. Always leave one full blank line between sections/paragraphs - never run two sections together on adjacent lines - so Teams renders clear paragraph spacing.

FOLLOW-UP QUESTIONS (Teams thread continuation) - once a human has replied in the case's Teams thread asking a follow-up question (e.g., "check for existing fraud flags on this policy", "what's the coverage on POL-00002", "is this a CAT event"), treat it as a normal question and apply the STRUCTURED DATA / GENERAL KNOWLEDGE / FRAUD-SUBROGATION / CAT-BULLETIN / SIU-ROUTING tool-routing rules above as applicable - this is when Fabric IQ, Foundry IQ, and Work IQ tool calls are expected and appropriate.
For a follow-up question, your reply MUST contain ONLY the direct answer to that specific follow-up question - a short lead-in sentence (if helpful) followed by the new information (using Markdown bullets/tables as appropriate for readability). Do NOT restate, repeat, summarize, or re-send the original NEW EMAIL INTAKE analysis (the "Hi, I am an agent..." greeting, From/Subject lines, Urgency Assessment, SIU Fraud Red Flags, Subrogation Review, or Next Steps sections) - the human has already seen that message earlier in the thread. Also do NOT repeat the follow-up question itself back to the user before answering it. Reply with the new answer only, once.
Apply the same readability formatting to follow-up answers as the initial analysis: a relevant leading emoji for the answer's topic (e.g. 📄 for policy/coverage details, 🚗 for vehicle details, 👤 for policyholder details, 🚩 for fraud/red-flag findings, 📚 for general knowledge/playbook answers, 📎 for document/bulletin findings, 📧 for a confirmation that an email was sent), **bold** for field labels or a short header, "- " bullets (with "  - " nested sub-bullets where useful) for lists of facts, a Markdown table for structured multi-field records (e.g. policy/vehicle/coverage details), and a blank line between paragraphs/sections. Keep it concise - do not add emojis or headers that aren't relevant to the specific answer.
If a follow-up question asks about MULTIPLE related entities for the same policy/claim (e.g., "coverage, vehicle, and policyholder details for POL-00005"), call the Fabric IQ ontology tool separately (or with an explicit multi-part question naming each entity type, e.g., "Return the Policy, Coverage, Vehicle, Policyholder, and Adjuster records related to POL-00005") for EACH entity type requested rather than a single vague query, so that entities are not silently dropped from the ontology tool's response. Only report an entity as unavailable if a targeted query for that specific entity type genuinely returns no result.

Do NOT suggest or take any action involving creating, filing, or opening a new claim record, or updating/writing back to claim or policy data - this solution is read-only against the Fabric IQ data and cannot create new claims.
"""


def _load_fabric_openapi_tool() -> OpenApiTool:
    spec_path = os.environ.get("FABRIC_ONTOLOGY_TOOL_OPENAPI_SPEC_PATH", "").strip() or os.path.join(
        os.path.dirname(__file__), "fabric_ontology_openapi.json"
    )
    with open(spec_path) as f:
        spec = json.load(f)
    # The Fabric ontology tool calls our own webapp's /query-ontology endpoint,
    # which is protected by an x-api-key header. Auth is via the Foundry
    # project connection "workiq-api-key-conn" (Custom Keys), which injects
    # the x-api-key header - no secret is embedded in the agent definition.
    return OpenApiTool(
        name="fabric_iq_ontology",
        description=(
            "Query the AutoFNOL_Ontology graph (Policyholder, Vehicle, Adjuster, "
            "RepairShop, Policy, Claim, FraudSignal, SubrogationFlag entities and "
            "their relationships) for structured policy/claim/adjuster data."
        ),
        spec=spec,
        auth=OpenApiConnectionAuthDetails(
            security_scheme=OpenApiConnectionSecurityScheme(connection_id=WORKIQ_API_KEY_CONNECTION_ID)
        ),
    )


def _load_workiq_openapi_tool() -> OpenApiTool:
    spec_path = os.path.join(os.path.dirname(__file__), "workiq_graph_openapi.json")
    with open(spec_path) as f:
        spec = json.load(f)
    return OpenApiTool(
        name="workiq_graph_search",
        description=(
            "Search SharePoint/OneDrive documents and Outlook mail via Microsoft "
            "Graph for fresh/ungoverned operational content (CAT bulletins, "
            "routing-change emails) not present in the governed Foundry IQ "
            "knowledge base. Also supports sending an escalation email (e.g. to "
            "SIU or an adjuster team) from the Agent Identity's own mailbox. "
            "See tools_workiq_graph.py for the underlying Graph calls this wraps."
        ),
        spec=spec,
        auth=OpenApiConnectionAuthDetails(
            security_scheme=OpenApiConnectionSecurityScheme(connection_id=WORKIQ_API_KEY_CONNECTION_ID)
        ),
    )


def _connected_foundry_iq_tool() -> ConnectedAgentTool:
    knowledge_agent_id = os.environ.get("FOUNDRY_KNOWLEDGE_AGENT_ID", "").strip()

    if not knowledge_agent_id:
        agent_id_file = os.environ.get(
            "FOUNDRY_KNOWLEDGE_AGENT_ID_FILE",
            os.path.join(os.path.dirname(__file__), "foundry_knowledge_agent_id.txt"),
        )
        with open(agent_id_file) as f:
            knowledge_agent_id = f.read().strip()

    # ConnectedAgentTool lets the orchestrator call the existing Foundry IQ
    # knowledge agent directly, agent-to-agent, within the same Foundry
    # project - no new knowledge agent, index, or connector is created here.
    return ConnectedAgentTool(
        id=knowledge_agent_id,
        name="foundry_iq_knowledge_agent",
        description=(
            "Governed knowledge agent for auto policy wording, coverage parts/"
            "exclusions, FNOL triage-tier rules, SIU fraud red flags, state "
            "regulatory requirements, and subrogation methodology. Always cites "
            "sources. Primary source of truth for general (non-policy-specific) "
            "questions."
        ),
    )


def main():
    credential = AzureCliCredential()
    agents_client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    tools = []
    tool_resources = {}

    fabric_tool = _load_fabric_openapi_tool()
    tools.extend(fabric_tool.definitions)

    foundry_iq_tool = _connected_foundry_iq_tool()
    tools.extend(foundry_iq_tool.definitions)

    workiq_tool = _load_workiq_openapi_tool()
    tools.extend(workiq_tool.definitions)

    # delete previous orchestrator agent version if recorded, to avoid orphans
    id_file = os.path.join(os.path.dirname(__file__), "orchestrator_agent_id.txt")
    if os.path.exists(id_file):
        with open(id_file) as f:
            old_id = f.read().strip()
        try:
            agents_client.delete_agent(old_id)
            print("Deleted old orchestrator agent:", old_id)
        except Exception as e:
            print("Could not delete old orchestrator agent (may not exist):", e)

    agent = agents_client.create_agent(
        model=MODEL_DEPLOYMENT,
        name=ORCHESTRATOR_AGENT_NAME,
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=tools,
        tool_resources=tool_resources or None,
    )
    print("Created orchestrator agent:", agent.id, agent.name)

    with open(id_file, "w") as f:
        f.write(agent.id)


if __name__ == "__main__":
    main()
