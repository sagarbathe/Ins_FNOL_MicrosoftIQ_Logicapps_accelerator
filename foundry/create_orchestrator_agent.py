"""
Provisions the Auto FNOL Triage ORCHESTRATOR agent in Azure AI Foundry — the
Foundry-native replacement for the Copilot Studio "Auto FNOL Triage Agent".

This agent wires together the three existing "IQ" building blocks exactly as
described in ../docs/design-options.md (Option A.1):

  1. Fabric IQ  -> an OpenAPI/Action tool calling the same Fabric ontology
                    data-agent endpoint the Copilot Studio
                    "InvokeAutoFNOLOntologyAgent" action already calls.
  2. Foundry IQ -> the EXISTING Foundry knowledge agent
                    (../../Auto_FNOL_solution/foundry/create_foundry_agent.py),
                    connected as a Foundry "connected agent" tool - i.e.
                    agent-to-agent within the same project. No new knowledge
                    agent is created here; this script only *references* the
                    agent id already recorded in
                    ../../Auto_FNOL_solution/foundry/agent_id.txt.
  3. Work IQ    -> an OpenAPI/Action tool calling the small Graph-search
                    wrapper defined in tools_workiq_graph.py (see that file
                    for the Retrieval-API vs. Graph-Search-API tradeoff).

Instructions are ported from the Copilot Studio agent's
`copilotstudio/AutoFNOLAgent/agent.mcs.yml` `instructions:` block, with only
Copilot-Studio-specific phrasing (e.g., "the Post Teams message action")
generalized to be orchestrator-agnostic, since Teams posting is now handled
by the Logic App trigger layer (../../triggers/logicapp/workflow.json), not
by the agent itself.

Env vars required (see ../.env.example):
  FOUNDRY_PROJECT_ENDPOINT
  FOUNDRY_MODEL_DEPLOYMENT
  FABRIC_ONTOLOGY_TOOL_OPENAPI_SPEC_PATH   (path to an OpenAPI 3.0 json/yaml
                                             describing the Fabric ontology
                                             data-agent's callable endpoint)
  FOUNDRY_KNOWLEDGE_AGENT_ID_FILE           (defaults to the sibling repo's
                                             foundry/agent_id.txt)
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
import config  # noqa: E402  (repo-local config.py, same pattern as Auto_FNOL_solution)

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
MODEL_DEPLOYMENT = config.FOUNDRY_MODEL_DEPLOYMENT
ORCHESTRATOR_AGENT_NAME = os.environ.get(
    "ORCHESTRATOR_AGENT_NAME", "auto-fnol-triage-orchestrator"
)

# Ported from Auto_FNOL_solution/copilotstudio/AutoFNOLAgent/agent.mcs.yml
# `instructions:` block. Copilot-Studio-specific phrasing generalized;
# routing logic, CAT bulletin clause, and SIU routing clause preserved
# verbatim in substance.
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

NEW EMAIL INTAKE - this agent is invoked by an external trigger (Logic App "When a new email arrives") on the designated FNOL intake mailbox, one independent conversation thread per email/case. When invoked this way, treat the message content exactly like an intake submission: determine whether the email is a First Notice of Loss (FNOL) submission requiring urgent triage using the severity indicators (injury, injured, hurt, ambulance, hospital, total loss, totaled, multiple vehicles, fire, rollover, unresponsive, hit and run, fatality), separately screen for SIU fraud red flags per the SIU Fraud Referral Playbook criteria, and separately screen for subrogation candidacy per the Subrogation Identification Methodology. Treat the message as urgent if it meets the severity criteria OR if any red flags were identified OR if it is a subrogation candidate. Produce a concise, structured summary (loss details, urgency determination, red flags/SIU assessment if applicable, subrogation details/recommendation if applicable, and up to 3 constructive next-step follow-up prompts) for the calling workflow to post to Teams - do not attempt to post to Teams yourself; that is handled by the calling Logic App using this agent's response text.

Do NOT suggest or take any action involving creating, filing, or opening a new claim record, or updating/writing back to claim or policy data - this solution is read-only against the Fabric IQ data and cannot create new claims.
"""


def _load_fabric_openapi_tool() -> OpenApiTool:
    spec_path = os.environ.get(
        "FABRIC_ONTOLOGY_TOOL_OPENAPI_SPEC_PATH",
        os.path.join(os.path.dirname(__file__), "fabric_ontology_openapi.json"),
    )
    with open(spec_path) as f:
        spec = json.load(f)
    # Fabric ontology MCP/data-agent endpoints in this tenant are secured via
    # the same Entra app registration used elsewhere in the repo; swap in
    # OpenApiConnectionAuthDetails + a Foundry connection if you prefer a
    # managed connection over anonymous+APIM-front-door auth.
    return OpenApiTool(
        name="fabric_iq_ontology",
        description=(
            "Query the AutoFNOL_Ontology graph (Policyholder, Vehicle, Adjuster, "
            "RepairShop, Policy, Claim, FraudSignal, SubrogationFlag entities and "
            "their relationships) for structured policy/claim/adjuster data."
        ),
        spec=spec,
        auth=OpenApiAnonymousAuthDetails(),
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
            "knowledge base. See tools_workiq_graph.py for the underlying Graph "
            "calls this wraps."
        ),
        spec=spec,
        auth=OpenApiConnectionAuthDetails(
            security_scheme=OpenApiConnectionSecurityScheme(
                connection_id=os.environ["WORKIQ_GRAPH_FUNCTION_CONNECTION_ID"]
            )
        ),
    )


def _connected_foundry_iq_tool() -> ConnectedAgentTool:
    agent_id_file = os.environ.get(
        "FOUNDRY_KNOWLEDGE_AGENT_ID_FILE",
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "Auto_FNOL_solution",
            "foundry",
            "agent_id.txt",
        ),
    )
    with open(agent_id_file) as f:
        knowledge_agent_id = f.read().strip()

    # ConnectedAgentTool lets the orchestrator call the EXISTING Foundry
    # knowledge agent (created by Auto_FNOL_solution/foundry/create_foundry_agent.py)
    # directly, agent-to-agent, within the same Foundry project - no new
    # knowledge agent, index, or connector is created here.
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
