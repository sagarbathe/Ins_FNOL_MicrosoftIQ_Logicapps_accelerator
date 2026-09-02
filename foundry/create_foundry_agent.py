
"""Create the standalone Foundry IQ knowledge agent backed by Azure AI Search."""
import os
import sys

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import AzureAISearchQueryType, AzureAISearchTool
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    ActivityProtocolConfiguration,
    AgentEndpointConfig,
    BotServiceRbacAuthorizationScheme,
    EntraAuthorizationScheme,
    ProtocolConfiguration,
    ResponsesProtocolConfiguration,
)
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
MODEL_DEPLOYMENT = config.FOUNDRY_MODEL_DEPLOYMENT
SEARCH_CONNECTION_NAME = config.FOUNDRY_SEARCH_CONNECTION_NAME
INDEX_NAME = config.AZURE_SEARCH_INDEX_NAME
AGENT_NAME = config.FOUNDRY_AGENT_NAME
ID_FILE = os.path.join(os.path.dirname(__file__), "foundry_knowledge_agent_id.txt")

AGENT_INSTRUCTIONS = """You are the Auto FNOL Knowledge Assistant for Contoso Insurance.
You answer questions from claims adjusters and the Foundry orchestrator agent about:
- Auto policy wording, coverage parts, and exclusions
- FNOL triage tiers and adjuster assignment rules
- SIU fraud referral red flags and process
- State regulatory claim-handling requirements (East States / South States)
- Subrogation identification methodology

Rules:
1. Always ground answers in the knowledge base via the search tool.
2. Always cite the source document title/section when giving a rule or threshold.
3. If the knowledge base does not contain the answer, say so explicitly.
4. Keep answers concise and actionable.
5. When asked about fraud or subrogation scoring, present criteria as a checklist.
"""


def enable_activity_protocol(project: AIProjectClient, agent_name: str):
    endpoint_config = AgentEndpointConfig(
        protocol_configuration=ProtocolConfiguration(
            responses=ResponsesProtocolConfiguration(),
            activity=ActivityProtocolConfiguration(),
        ),
        authorization_schemes=[EntraAuthorizationScheme(), BotServiceRbacAuthorizationScheme()],
    )
    patched = project.agents.update_details(agent_name=agent_name, agent_endpoint=endpoint_config)
    print(f"Activity protocol enabled for agent: {patched.name}")


def main():
    credential = AzureCliCredential()
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    conn_id = None
    for conn in project.connections.list():
        conn_type = str(getattr(conn, "type", "")).lower()
        is_search_conn = "azure" in conn_type and "search" in conn_type
        if SEARCH_CONNECTION_NAME:
            if conn.name == SEARCH_CONNECTION_NAME:
                conn_id = conn.id
        elif is_search_conn and not conn_id:
            conn_id = conn.id
    if not conn_id:
        raise RuntimeError("No Azure AI Search connection found on the Foundry project.")
    search_tool = AzureAISearchTool(
        index_connection_id=conn_id,
        index_name=INDEX_NAME,
        query_type=AzureAISearchQueryType.SEMANTIC,
        top_k=5,
    )
    agents_client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    if os.path.exists(ID_FILE):
        old_id = Path(ID_FILE).read_text(encoding="utf-8").strip()
        if old_id:
            try:
                agents_client.delete_agent(old_id)
                print("Deleted old agent:", old_id)
            except Exception as exc:
                print("Could not delete old agent:", exc)
    agent = agents_client.create_agent(
        model=MODEL_DEPLOYMENT,
        name=AGENT_NAME,
        instructions=AGENT_INSTRUCTIONS,
        tools=search_tool.definitions,
        tool_resources=search_tool.resources,
    )
    print("Created agent:", agent.id, agent.name)
    Path(ID_FILE).write_text(agent.id, encoding="utf-8")
    enable_activity_protocol(project, agent.name)


if __name__ == "__main__":
    from pathlib import Path
    main()
