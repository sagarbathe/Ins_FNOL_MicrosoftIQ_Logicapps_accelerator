"""
Creates (or updates) the Foundry project Connection used by the
orchestrator agent's OpenAPI tools to authenticate to the Work IQ webapp
(app-autofnol-workiq) via an x-api-key header, without embedding the key
in the agent definition or source control.

The azure-ai-projects SDK's ConnectionsOperations is currently READ-ONLY
(get/get_default/list only - no create/update as of azure-ai-projects
2.5.0), so this creates the connection via a direct ARM REST PUT.

Usage:
    python foundry/create_workiq_connection.py

Reads from .env:
    FOUNDRY_PROJECT_ENDPOINT   (used only to help sanity-check env is loaded)
    WORKIQ_API_KEY             (the secret injected as the x-api-key header)

Also requires these to be set (see .env.example) or passed as CLI args:
    --subscription-id, --resource-group, --account-name, --project-name
    --workiq-base-url  (default: https://app-autofnol-workiq.azurewebsites.net)
    --connection-name  (default: workiq-api-key-conn)

Requires the caller to be logged in via `az login` with Contributor (or
higher) on the Foundry account/project.
"""
import argparse
import os
import sys

import requests
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription-id", default=os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
    parser.add_argument("--resource-group", default=os.environ.get("FOUNDRY_RESOURCE_GROUP", ""))
    parser.add_argument("--account-name", default=os.environ.get("FOUNDRY_ACCOUNT_NAME", ""))
    parser.add_argument("--project-name", default=os.environ.get("FOUNDRY_PROJECT_NAME", ""))
    parser.add_argument(
        "--workiq-base-url",
        default=os.environ.get("WORKIQ_BASE_URL", "https://app-autofnol-workiq.azurewebsites.net"),
    )
    parser.add_argument("--connection-name", default="workiq-api-key-conn")
    args = parser.parse_args()

    for required, val in [
        ("--subscription-id / AZURE_SUBSCRIPTION_ID", args.subscription_id),
        ("--resource-group / FOUNDRY_RESOURCE_GROUP", args.resource_group),
        ("--account-name / FOUNDRY_ACCOUNT_NAME", args.account_name),
        ("--project-name / FOUNDRY_PROJECT_NAME", args.project_name),
    ]:
        if not val:
            raise SystemExit(f"Missing required value: {required}")

    api_key = os.environ.get("WORKIQ_API_KEY", "")
    if not api_key:
        raise SystemExit("WORKIQ_API_KEY is not set in .env - set it before creating the connection.")

    credential = AzureCliCredential()
    token = credential.get_token("https://management.azure.com/.default").token

    url = (
        f"https://management.azure.com/subscriptions/{args.subscription_id}"
        f"/resourceGroups/{args.resource_group}/providers/Microsoft.CognitiveServices"
        f"/accounts/{args.account_name}/projects/{args.project_name}"
        f"/connections/{args.connection_name}?api-version=2025-04-01-preview"
    )
    body = {
        "properties": {
            "category": "CustomKeys",
            "authType": "CustomKeys",
            "target": args.workiq_base_url,
            "isSharedToAll": True,
            "credentials": {"keys": {"x-api-key": api_key}},
        }
    }
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Connection '{args.connection_name}' created/updated successfully.")
    print(f"Connection id: {url.split('?')[0]}")
    print(
        "Set this connection id (or leave create_orchestrator_agent.py's default, "
        "which already points at this same subscription/RG/account/project) before "
        "running foundry/create_orchestrator_agent.py."
    )


if __name__ == "__main__":
    main()
