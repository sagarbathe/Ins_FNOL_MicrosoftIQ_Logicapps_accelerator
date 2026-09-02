"""
E2E test harness for Auto FNOL Triage.

For each sample scenario:
  1. Sends the sample email (as the Agent Identity, to its own mailbox) via Graph.
  2. Waits for the la-fnol-email-intake Logic App to pick it up and process it
     (Create_Foundry_thread -> Post_email_as_message -> Start_run -> ... ->
     Post_new_case_alert_to_Teams -> Persist_CaseThreadMap_row).
  3. Confirms the run succeeded and captures the agent's initial response.
  4. Posts each follow-up question into the SAME Foundry thread (simulating
     what the Teams-reply-poller flow does) and captures the agent's answer.

Uses AzureCliCredential (the signed-in operator) against the Foundry project
directly via the azure-ai-agents SDK - this is independent of, and does not
modify, the Logic Apps' own Managed-Identity Foundry calls being tested.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402

from azure.identity import AzureCliCredential  # noqa: E402
from azure.ai.agents import AgentsClient  # noqa: E402
import msal  # noqa: E402
import requests  # noqa: E402

REPO_ROOT = os.path.dirname(__file__)

AGENT_ID_FILE = os.path.join(REPO_ROOT, "foundry", "orchestrator_agent_id.txt")
with open(AGENT_ID_FILE, "r", encoding="utf-8") as f:
    ORCHESTRATOR_AGENT_ID = f.read().strip()

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]

RG = os.environ.get("WORKIQ_RESOURCE_GROUP") or os.environ.get("LOGICAPP_RESOURCE_GROUP")
SUB_ID = os.environ["AZURE_SUBSCRIPTION_ID"]
WORKFLOW_NAME = "la-fnol-email-intake"

# ---- Agent Identity mail-send (reuse the same MSAL public client + cache) ----
_PUBLIC_CLIENT_ID = os.environ.get("AGENT_IDENTITY_PUBLIC_CLIENT_ID", "414bf9dc-65ae-4338-8b38-6fc0a353a32a")
_TENANT_ID = os.environ["AGENT_IDENTITY_TENANT_ID"]
_CACHE_PATH = os.path.join(REPO_ROOT, ".secrets", "agent_identity_token_cache.bin")
_AGENT_UPN = os.environ["AGENT_IDENTITY_UPN"]


def _agent_graph_token():
    cache = msal.SerializableTokenCache()
    with open(_CACHE_PATH, "r", encoding="utf-8") as f:
        cache.deserialize(f.read())
    app = msal.PublicClientApplication(
        client_id=_PUBLIC_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    result = app.acquire_token_silent(["https://graph.microsoft.com/Mail.Send"], account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(f"Failed to get Mail.Send token: {result}")
    return result["access_token"]


def send_test_email(subject, body_text, from_display="Test Sender <test@example.net>"):
    token = _agent_graph_token()
    msg = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_text},
            "toRecipients": [{"emailAddress": {"address": _AGENT_UPN}}],
        },
        "saveToSentItems": "true",
    }
    r = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=msg,
        timeout=30,
    )
    r.raise_for_status()
    print(f"  Sent test email: {subject!r}")


# ---- Logic App run polling (az CLI via subprocess, reuse existing az login) ----
import subprocess  # noqa: E402


def _az_json(args):
    result = subprocess.run(["az.cmd"] + args + ["-o", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"az failed: {result.stderr}")
    return json.loads(result.stdout)


def wait_for_new_run(after_time_iso, timeout_s=180, poll_s=5):
    """Polls Logic App run history for a new run with startTime after
    after_time_iso. Returns the run dict once found (any terminal or
    non-terminal status)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = _az_json([
            "resource", "invoke-action",
            "--action", "getRunHistory",
            "--ids",
            f"/subscriptions/{SUB_ID}/resourceGroups/{RG}/providers/Microsoft.Logic/workflows/{WORKFLOW_NAME}",
        ]) if False else None
        # Use REST list instead (invoke-action for run history isn't a thing) - use az rest.
        r = subprocess.run(
            ["az.cmd", "rest", "--method", "get", "--url",
             f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{RG}"
             f"/providers/Microsoft.Logic/workflows/{WORKFLOW_NAME}/runs?api-version=2019-05-01"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            for run in data.get("value", []):
                st = run["properties"]["startTime"]
                if st > after_time_iso:
                    return run
        time.sleep(poll_s)
    return None


def wait_for_run_terminal(run_name, timeout_s=180, poll_s=5):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = subprocess.run(
            ["az.cmd", "rest", "--method", "get", "--url",
             f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{RG}"
             f"/providers/Microsoft.Logic/workflows/{WORKFLOW_NAME}/runs/{run_name}?api-version=2019-05-01"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            status = data["properties"]["status"]
            if status in ("Succeeded", "Failed", "Cancelled"):
                return data
        time.sleep(poll_s)
    return None


def get_run_actions(run_name):
    r = subprocess.run(
        ["az.cmd", "rest", "--method", "get", "--url",
         f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{RG}"
         f"/providers/Microsoft.Logic/workflows/{WORKFLOW_NAME}/runs/{run_name}/actions?api-version=2019-05-01"],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)["value"]


# ---- Foundry thread helpers (find thread by CaseThreadMap, ask follow-ups) ----
credential = AzureCliCredential()
agents_client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)


def get_case_thread_id_from_persist_action(run_name):
    """Reads the Persist_CaseThreadMap_row action inputs to find the
    foundryThreadId that was mapped for this run."""
    actions = get_run_actions(run_name)
    for a in actions:
        if a["name"] == "Persist_CaseThreadMap_row":
            inputs_link = a["properties"].get("inputsLink")
            if not inputs_link:
                return None
            rr = requests.get(inputs_link["uri"], timeout=30)
            body = rr.json().get("body", {})
            return body.get("foundryThreadId")
    return None


def ask_followup(thread_id, question):
    agents_client.messages.create(thread_id=thread_id, role="user", content=question)
    run = agents_client.runs.create(thread_id=thread_id, agent_id=ORCHESTRATOR_AGENT_ID)
    deadline = time.time() + 120
    while run.status in ("queued", "in_progress", "requires_action") and time.time() < deadline:
        time.sleep(2)
        run = agents_client.runs.get(thread_id=thread_id, run_id=run.id)
    if run.status != "completed":
        return f"[RUN NOT COMPLETED: status={run.status}, last_error={getattr(run, 'last_error', None)}]"
    messages = list(agents_client.messages.list(thread_id=thread_id, order="desc", limit=1))
    if not messages:
        return "[NO MESSAGE RETURNED]"
    msg = messages[0]
    parts = []
    for c in msg.content:
        if hasattr(c, "text"):
            parts.append(c.text.value)
    return "\n".join(parts)
