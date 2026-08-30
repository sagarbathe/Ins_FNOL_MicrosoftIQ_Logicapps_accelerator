"""
Creates the Fabric SP-auth diagnostic notebook as a real Notebook item in the
WS_AutoFNOL workspace, using the Fabric REST API (Create Item with a
notebook-content definition).

Run: python deploy_diagnostic_notebook.py
Requires: az login (uses the caller's own delegated Azure CLI credential to
create the item - only the *notebook's own cells*, once run inside Fabric,
use the service principal).
"""
import base64
import json
import subprocess
import time

import requests

WORKSPACE_ID = "4fb5c773-27f6-4f0d-9eb0-f040abfdd977"  # WS_AutoFNOL
NOTEBOOK_NAME = "SP_Auth_Diagnostic_FabricIQ"
NOTEBOOK_PATH = "fabric_sp_auth_diagnostic_notebook.ipynb"


def get_fabric_token() -> str:
    import os
    env_token = os.environ.get("FABRIC_TOKEN")
    if env_token:
        return env_token.strip()
    result = subprocess.run(
        ["az.cmd", "account", "get-access-token", "--resource",
         "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True, shell=False,
    )
    return result.stdout.strip()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def main():
    token = get_fabric_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with open(NOTEBOOK_PATH, "rb") as f:
        notebook_bytes = f.read()

    platform_payload = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Notebook", "displayName": NOTEBOOK_NAME},
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }
    platform_bytes = json.dumps(platform_payload).encode("utf-8")

    body = {
        "displayName": NOTEBOOK_NAME,
        "description": (
            "Diagnostic: auth as AutoFNOL Graph SP, then query the Lakehouse, "
            "the Fabric Data Agent MCP endpoint, and the Ontology MCP endpoint."
        ),
        "definition": {
            "format": "ipynb",
            "parts": [
                {
                    "path": "notebook-content.ipynb",
                    "payload": b64(notebook_bytes),
                    "payloadType": "InlineBase64",
                },
                {
                    "path": ".platform",
                    "payload": b64(platform_bytes),
                    "payloadType": "InlineBase64",
                },
            ],
        },
    }

    url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items"
    resp = requests.post(url, headers=headers, json=body)

    if resp.status_code == 201:
        item = resp.json()
        print("Notebook created successfully.")
        print("Item id:", item.get("id"))
        print("Display name:", item.get("displayName"))
        return

    if resp.status_code == 202:
        # Long-running operation - poll it.
        op_location = resp.headers.get("Location")
        retry_after = int(resp.headers.get("Retry-After", "5"))
        print(f"Notebook creation is a long-running operation. Polling {op_location} ...")
        while True:
            time.sleep(retry_after)
            poll = requests.get(op_location, headers=headers)
            poll.raise_for_status()
            state = poll.json()
            status = state.get("status")
            print("  status:", status)
            if status == "Succeeded":
                result_resp = requests.get(op_location + "/result", headers=headers)
                if result_resp.ok:
                    print("Result:", json.dumps(result_resp.json(), indent=2))
                print("Notebook created successfully.")
                return
            if status == "Failed":
                print("Notebook creation failed:", json.dumps(state, indent=2))
                return
        return

    print(f"Notebook creation failed: HTTP {resp.status_code}")
    print(resp.text)


if __name__ == "__main__":
    main()
