"""
Automates as much of the Logic Apps deployment as possible:

  1. Generates triggers/logicapp/parameters.json from .env + the recorded
     orchestrator agent id (foundry/orchestrator_agent_id.txt) - no manual
     editing needed for this file.
  2. Creates the Office 365 API Connection resource via ARM REST (idempotent
     - skips if it already exists) and writes its resourceId into
     triggers/logicapp/connections.json.
  3. Creates (or updates) both Consumption Logic App resources
     (workflow.json and workflow-teams-reply-poller.json) via ARM REST,
     wiring in parameters.json + connections.json.

What STILL requires a human (cannot be automated):
  - Authorizing the Office 365 connection (OAuth consent - must happen in a
    browser as a signed-in user; there is no non-interactive/service
    principal grant flow for this connector).
  - Granting Logic App #2's managed identity the Graph APPLICATION
    permission ChannelMessage.Read.All (Step 9 handles the prompt/docs for
    this - it is a Graph app-role grant, not an ARM operation, and Microsoft
    requires either admin-portal consent or a Graph API call using an
    already-consented caller).

Usage:
    python triggers/logicapp/deploy_logic_apps.py

Requires (from .env or already-created files):
    FOUNDRY_PROJECT_ENDPOINT, TEAMS_TEAM_ID, TEAMS_CHANNEL_ID,
    WORKIQ_BASE_URL, WORKIQ_API_KEY, AGENT_IDENTITY_UPN,
    AZURE_SUBSCRIPTION_ID, resource group to deploy into (--resource-group
    or LOGICAPP_RESOURCE_GROUP), and foundry/orchestrator_agent_id.txt
    (created by Step 7).
"""
import argparse
import json
import os
import sys
import time

import requests
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config  # noqa: E402

LOGICAPP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(LOGICAPP_DIR, "..", "..")
ARM_API_VERSION_CONN = "2016-06-01"
ARM_API_VERSION_LOGICAPP = "2019-05-01"


def _cred_token(credential):
    return credential.get_token("https://management.azure.com/.default").token


def _strip_comments(obj):
    """Recursively removes '_comment' keys from a JSON-like structure.

    workflow.json / workflow-teams-reply-poller.json embed '_comment' fields
    purely for human documentation, but the Logic Apps ARM schema
    (FlowTemplate) rejects any unrecognized property - including inside
    nested parameter definitions - so these must be stripped before the
    definition is sent to ARM. The source files on disk are left untouched.
    """
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if k != "_comment"}
    if isinstance(obj, list):
        return [_strip_comments(item) for item in obj]
    return obj


def generate_parameters_json():
    """Fully auto-generates parameters.json from .env + orchestrator_agent_id.txt.
    No manual editing required for this file."""
    agent_id_path = os.path.join(REPO_ROOT, "foundry", "orchestrator_agent_id.txt")
    if not os.path.exists(agent_id_path):
        raise SystemExit(
            f"Missing {agent_id_path} - run Step 7 (foundry/create_orchestrator_agent.py) first."
        )
    with open(agent_id_path, "r", encoding="utf-8") as f:
        orchestrator_agent_id = f.read().strip()

    required_env = {
        "foundryProjectEndpoint": os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
        "teamId": os.environ.get("TEAMS_TEAM_ID", ""),
        "channelId": os.environ.get("TEAMS_CHANNEL_ID", ""),
        "fnolMailboxUserId": os.environ.get("AGENT_IDENTITY_UPN", ""),
        "agentIdentityUserId": os.environ.get("AGENT_IDENTITY_OBJECT_ID", ""),
        "workiqBaseUrl": os.environ.get("WORKIQ_BASE_URL", ""),
        "workiqApiKey": os.environ.get("WORKIQ_API_KEY", ""),
    }
    missing = [k for k, v in required_env.items() if not v]
    if missing:
        raise SystemExit(f"Missing required .env values for parameters.json: {missing}")

    params = {
        "foundryProjectEndpoint": {"value": required_env["foundryProjectEndpoint"]},
        "orchestratorAgentIdFilePlaceholder": {"value": orchestrator_agent_id},
        "teamId": {"value": required_env["teamId"]},
        "channelId": {"value": required_env["channelId"]},
        "fnolMailboxUserId": {"value": required_env["fnolMailboxUserId"]},
        "agentIdentityUserId": {"value": required_env["agentIdentityUserId"]},
        "workiqBaseUrl": {"value": required_env["workiqBaseUrl"]},
        "workiqApiKey": {"value": required_env["workiqApiKey"]},
    }
    out_path = os.path.join(LOGICAPP_DIR, "parameters.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
    print(f"Generated {out_path} automatically from .env + orchestrator_agent_id.txt.")
    return params


def ensure_office365_connection(credential, subscription_id, resource_group, location):
    """Creates the Office 365 API Connection resource if it doesn't already
    exist (idempotent). Returns (connection_resource_id, managed_api_id)."""
    conn_name = "office365-1"
    token = _cred_token(credential)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    managed_api_id = (
        f"/subscriptions/{subscription_id}/providers/Microsoft.Web"
        f"/locations/{location}/managedApis/{conn_name}"
    )
    conn_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Web/connections/{conn_name}"
    )
    url = f"https://management.azure.com{conn_resource_id}?api-version={ARM_API_VERSION_CONN}"

    existing = requests.get(url, headers=headers, timeout=30)
    if existing.status_code == 200:
        print(f"Office 365 API connection '{conn_name}' already exists - skipping creation.")
        return conn_resource_id, managed_api_id

    body = {
        "location": location,
        "properties": {
            "displayName": "FNOL Office 365 connection",
            "api": {"id": managed_api_id},
        },
    }
    resp = requests.put(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    print(f"Created Office 365 API connection '{conn_name}'.")
    print(
        "MANUAL ACTION STILL REQUIRED: authorize this connection (OAuth sign-in as the FNOL "
        "intake mailbox account) - Portal -> Resource groups -> "
        f"{resource_group} -> {conn_name} -> Edit API connection -> Authorize -> Save."
    )
    return conn_resource_id, managed_api_id


def write_connections_json(conn_resource_id, managed_api_id):
    out_path = os.path.join(LOGICAPP_DIR, "connections.json")
    body = {
        "$schema": "https://schema.management.azure.com/schemas/2015-01-01/deploymentTemplate.json#",
        "office365": {
            "connectionId": conn_resource_id,
            "connectionName": "office365-1",
            "id": managed_api_id,
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    print(f"Generated {out_path} automatically.")


def deploy_logic_app(credential, subscription_id, resource_group, location, name, workflow_path, parameters, conn_resource_id, managed_api_id):
    """Creates/updates a Consumption Logic App resource via ARM REST, wiring
    the workflow definition + $connections parameter values."""
    with open(workflow_path, "r", encoding="utf-8") as f:
        definition = json.load(f)
    definition = _strip_comments(definition)

    token = _cred_token(credential)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Logic/workflows/{name}"
    )
    url = f"https://management.azure.com{resource_id}?api-version={ARM_API_VERSION_LOGICAPP}"

    declared_params = definition.get("parameters", {})
    parameters_values = {k: v for k, v in parameters.items() if k in declared_params}
    # Only inject $connections if the workflow definition actually declares it
    # (i.e. it uses an ApiConnection trigger/action, like workflow.json's Office
    # 365 trigger) - workflow-teams-reply-poller.json uses no API Connections
    # (pure Managed Identity + HTTP), so ARM would reject an undeclared parameter.
    if "$connections" in declared_params:
        parameters_values["$connections"] = {
            "value": {
                "office365": {
                    "connectionId": conn_resource_id,
                    "connectionName": "office365-1",
                    "id": managed_api_id,
                }
            }
        }

    body = {
        "location": location,
        "identity": {"type": "SystemAssigned"},
        "properties": {
            "definition": definition,
            "parameters": parameters_values,
        },
    }
    resp = requests.put(url, headers=headers, json=body, timeout=60)
    if not resp.ok:
        print(f"ARM error response for '{name}': {resp.text}", file=sys.stderr)
    resp.raise_for_status()
    result = resp.json()
    principal_id = result.get("identity", {}).get("principalId", "<not yet available - re-check Portal>")
    print(f"Deployed Logic App '{name}'. Managed identity principalId: {principal_id}")
    return principal_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription-id", default=os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
    parser.add_argument("--resource-group", default=os.environ.get("LOGICAPP_RESOURCE_GROUP", os.environ.get("WORKIQ_RESOURCE_GROUP", "")))
    parser.add_argument("--location", default=os.environ.get("LOGICAPP_LOCATION", "eastus2"))
    parser.add_argument("--email-intake-name", default=os.environ.get("LOGICAPP_EMAIL_INTAKE_NAME", "la-fnol-email-intake"))
    parser.add_argument("--reply-poller-name", default=os.environ.get("LOGICAPP_REPLY_POLLER_NAME", "la-fnol-teams-reply-poller"))
    args = parser.parse_args()

    for required, val in [
        ("--subscription-id / AZURE_SUBSCRIPTION_ID", args.subscription_id),
        ("--resource-group / LOGICAPP_RESOURCE_GROUP or WORKIQ_RESOURCE_GROUP", args.resource_group),
    ]:
        if not val:
            raise SystemExit(f"Missing required value: {required}")

    credential = AzureCliCredential()

    print("Step A: generating parameters.json from .env + orchestrator_agent_id.txt...")
    params = generate_parameters_json()

    print("\nStep B: ensuring Office 365 API connection exists...")
    conn_resource_id, managed_api_id = ensure_office365_connection(
        credential, args.subscription_id, args.resource_group, args.location
    )
    write_connections_json(conn_resource_id, managed_api_id)

    print("\nStep C: deploying both Logic Apps...")
    la1_principal = deploy_logic_app(
        credential, args.subscription_id, args.resource_group, args.location,
        args.email_intake_name,
        os.path.join(LOGICAPP_DIR, "workflow.json"),
        params, conn_resource_id, managed_api_id,
    )
    time.sleep(5)
    la2_principal = deploy_logic_app(
        credential, args.subscription_id, args.resource_group, args.location,
        args.reply_poller_name,
        os.path.join(LOGICAPP_DIR, "workflow-teams-reply-poller.json"),
        params, conn_resource_id, managed_api_id,
    )

    print("\n" + "=" * 70)
    print("Logic Apps deployed. REMAINING MANUAL STEPS:")
    print("  1. Authorize the Office 365 connection (see message above) if not already done.")
    print(f"  2. Logic App #1 ('{args.email_intake_name}') managed identity principalId: {la1_principal}")
    print(f"     -> used in Step 9 to grant 'Cognitive Services User' on the Foundry account.")
    print(f"  3. Logic App #2 ('{args.reply_poller_name}') managed identity principalId: {la2_principal}")
    print("     -> used in Step 9 to grant 'Cognitive Services User' on the Foundry account (needed for its")
    print("        Post_reply_to_Foundry_thread/Start_followup_run/Get_followup_run_status/Get_followup_latest_message")
    print("        actions) AND the Graph app role ChannelMessage.Read.All.")
    print("=" * 70)


if __name__ == "__main__":
    main()
