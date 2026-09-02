"""
Enable the Activity Protocol + BotServiceRbac authorization scheme on a
Microsoft Foundry agent's stable endpoint.

WHY THIS IS NEEDED
------------------
Every Foundry agent gets a stable endpoint the moment it's created, but by
default that endpoint only serves the "Responses" protocol with "Entra"
authorization. Copilot Studio's "Connect to a Microsoft Foundry agent" feature
(Agents page -> Add an agent -> Microsoft Foundry) talks to the agent over the
**Activity Protocol**, using the **BotServiceRbac** (or BotServiceTenant)
authorization scheme.

If those aren't explicitly enabled on the agent endpoint, Copilot Studio's
Test playground fails with:

    Agent <name> endpoint does not support activity. Please update the
    agent endpoint to support this protocol.

...even though the agent works fine when tested directly in the Foundry
playground, and even though it has a perfectly valid MCP/knowledge-base tool
attached. Attaching tools/knowledge sources does NOT enable the Activity
protocol - it is a separate, independent setting on the agent endpoint.

NOTE: For NEW agents, this step is now folded directly into
foundry/create_foundry_agent.py (enable_activity_protocol()) and runs
automatically when the agent is created — you no longer need to run this
script separately. Keep it only to patch an existing agent that was created
before that change, or to target a different agent by name.

As of this writing (2026), enabling protocols/authorization schemes is NOT
yet exposed in the Foundry portal UI - it must be done via the REST API or
the Python SDK, as shown below.

Reference: https://learn.microsoft.com/azure/foundry/agents/how-to/configure-agent

USAGE
-----
    python enable_activity_protocol.py

Then in Copilot Studio: remove and re-add the Foundry agent connection
(Agents page) if you'd previously added it while activity protocol was
disabled, republish, and retest.
"""
import os
import sys
from azure.identity import AzureCliCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    ActivityProtocolConfiguration,
    AgentEndpointConfig,
    BotServiceRbacAuthorizationScheme,
    EntraAuthorizationScheme,
    ProtocolConfiguration,
    ResponsesProtocolConfiguration,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
AGENT_NAME = config.FOUNDRY_AGENT_NAME


def main():
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=AzureCliCredential())

    endpoint_config = AgentEndpointConfig(
        protocol_configuration=ProtocolConfiguration(
            # Keep Responses enabled (used by the Foundry playground / direct API calls)
            responses=ResponsesProtocolConfiguration(),
            # Required for Copilot Studio / Microsoft 365 / Teams integration
            activity=ActivityProtocolConfiguration(),
        ),
        authorization_schemes=[
            # Entra is the default; keep it for direct API/SDK callers
            EntraAuthorizationScheme(),
            # BotServiceRbac is required for Copilot Studio's Foundry agent connector.
            # NOTE: an agent endpoint can only have ONE BotService-level scheme
            # (BotServiceRbac OR BotServiceTenant, not both) - the API will
            # reject the request with a 400 if you try to set both.
            BotServiceRbacAuthorizationScheme(),
        ],
    )

    patched = client.agents.update_details(agent_name=AGENT_NAME, agent_endpoint=endpoint_config)
    print(f"Updated agent: {patched.name}")
    print(patched.agent_endpoint)


if __name__ == "__main__":
    main()
