"""
REST facade wrapping the Fabric IQ MCP data-agent endpoint, so it can be
called as a plain OpenAPI/Action tool from the Foundry orchestrator agent
(azure-ai-agents 1.1.0 does not yet expose a first-class MCP tool type).

Internally, this proxies to the real Fabric MCP endpoint
(https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace}/dataagents/{agent}/agent)
using the mcp Python package's streamable-http client, authenticated via a
Managed Identity / service principal Entra token for the
https://api.fabric.microsoft.com/.default scope (falls back to
AAD_CLIENT_ID/AAD_CLIENT_SECRET app registration in this deployment).

Public HTTPS endpoint only - no VNet/Private Endpoint used or required.
"""
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
from msal import ConfidentialClientApplication
import httpx

FABRIC_WORKSPACE_ID = os.environ.get("FABRIC_WORKSPACE_ID", "")
FABRIC_DATA_AGENT_ID = os.environ.get("FABRIC_DATA_AGENT_ID", "")

MCP_URL = (
    f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{FABRIC_WORKSPACE_ID}"
    f"/dataagents/{FABRIC_DATA_AGENT_ID}/agent"
)


def _get_fabric_token() -> str:
    tenant_id = os.environ["AAD_TENANT_ID"]
    client_id = os.environ["AAD_CLIENT_ID"]
    client_secret = os.environ["AAD_CLIENT_SECRET"]

    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(
        scopes=["https://api.fabric.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Fabric token: {result}")
    return result["access_token"]


async def _query_ontology_async(question: str) -> str:
    token = _get_fabric_token()

    def client_factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token}, timeout=60
        )

    async with streamablehttp_client(MCP_URL, http_client=client_factory()) as (
        read,
        write,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not tools.tools:
                return "Fabric IQ data agent has no callable tools."
            tool = tools.tools[0]
            arg_name = list(tool.input_schema.get("properties", {}).keys())[0]
            result = await session.call_tool(tool.name, {arg_name: question})
            texts = [getattr(c, "text", str(c)) for c in result.content]
            return "\n".join(texts)


def query_ontology(question: str) -> str:
    """Synchronous wrapper for the Flask route."""
    return asyncio.run(_query_ontology_async(question))
