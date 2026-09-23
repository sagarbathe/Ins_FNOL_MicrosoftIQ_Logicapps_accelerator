"""Update the EXISTING orchestrator agent in place (same agent id) with the
new instructions + workiq tool spec, instead of delete+recreate (which would
change the agent id and require redeploying the Logic Apps' assistantId
parameter). Reuses the tool-building helpers and ORCHESTRATOR_INSTRUCTIONS
from create_orchestrator_agent.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from azure.ai.agents import AgentsClient
from azure.identity import AzureCliCredential

import create_orchestrator_agent as coa

agents_client = AgentsClient(endpoint=coa.PROJECT_ENDPOINT, credential=AzureCliCredential())

id_file = os.path.join(os.path.dirname(__file__), "orchestrator_agent_id.txt")
with open(id_file) as f:
    agent_id = f.read().strip()

tools = []
tools.extend(coa._load_fabric_openapi_tool().definitions)
tools.extend(coa._connected_foundry_iq_tool().definitions)
tools.extend(coa._load_workiq_openapi_tool().definitions)

updated = agents_client.update_agent(
    agent_id,
    instructions=coa.ORCHESTRATOR_INSTRUCTIONS,
    tools=tools,
)
print("Updated orchestrator agent in place:", updated.id, updated.name)
