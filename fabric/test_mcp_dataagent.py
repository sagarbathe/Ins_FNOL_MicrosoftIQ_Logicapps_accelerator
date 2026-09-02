import asyncio
import os

from azure.identity import AzureCliCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

workspace_id = config.FABRIC_WORKSPACE_ID
data_agent_id = config.FABRIC_DATA_AGENT_ID
question = sys.argv[1] if len(sys.argv) > 1 else "Retrieve policy details and coverage for Policy POL-00005 to confirm injury and medical payment provisions"

assert config.FABRIC_DATA_AGENT_ID, "Set FABRIC_DATA_AGENT_ID in .env after creating the Data Agent item in the Fabric portal"

mcp_url = (
    f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace_id}"
    f"/dataagents/{data_agent_id}/agent"
)

credential = AzureCliCredential()


def get_auth_headers():
    token = credential.get_token("https://api.fabric.microsoft.com/.default")
    return {"Authorization": f"Bearer {token.token}"}


async def query_data_agent(question):
    headers = get_auth_headers()
    import httpx as httpx2

    def client_factory(headers=None, timeout=None, auth=None):
        return httpx2.AsyncClient(headers=get_auth_headers(), timeout=60)

    async with streamablehttp_client(mcp_url, http_client=client_factory()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])
            tool = tools.tools[0]
            print("Tool input schema:", tool.input_schema)
            arg_name = list(tool.input_schema.get("properties", {}).keys())[0]
            result = await session.call_tool(tool.name, {arg_name: question})
            print("isError:", getattr(result, "isError", None))
            print("full result object:", result)
            for c in result.content:
                print(getattr(c, "text", c))


asyncio.run(query_data_agent(question))
