"""
Shared helper for posting Auto FNOL Triage alerts/replies to Microsoft Teams
via Microsoft Graph, implementing the per-case thread-isolation design in
docs/teams-concurrency-design.md.

Two entry points:
  - post_new_case_alert(...)   -> posts a NEW top-level channel message for a
                                    brand-new case, and returns the root
                                    message id to persist in CaseThreadMap.
  - post_case_reply(...)       -> posts a REPLY nested under an existing
                                    case's root message (never a new
                                    top-level message), given the root
                                    message id looked up from CaseThreadMap.

Requires an app registration with Graph application permissions:
  ChannelMessage.Send, Chat.ReadWrite (or delegated equivalents), consented
  in the tenant. Auth is via MSAL client-credentials (app-only) using the
  same pattern as the other scripts in this repo's `foundry/` folder.
"""

import os
import requests
from msal import ConfidentialClientApplication

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _get_graph_token() -> str:
    tenant_id = os.environ["AAD_TENANT_ID"]
    client_id = os.environ["AAD_CLIENT_ID"]
    client_secret = os.environ["AAD_CLIENT_SECRET"]

    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Graph token: {result}")
    return result["access_token"]


def post_new_case_alert(team_id: str, channel_id: str, message_html: str) -> str:
    """Posts a new top-level channel message (the root of a case's reply
    chain) and returns the created message's id.

    Callers MUST persist the returned id (together with team_id, channel_id,
    the case id, and the Foundry thread id) in CaseThreadMap immediately.
    """
    token = _get_graph_token()
    url = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "html", "content": message_html}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def post_case_reply(team_id: str, channel_id: str, root_message_id: str, message_html: str) -> str:
    """Posts a reply nested under an existing case's root message, keeping
    it visually and structurally grouped under that case's thread instead of
    creating a new top-level message.
    """
    token = _get_graph_token()
    url = (
        f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}"
        f"/messages/{root_message_id}/replies"
    )
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "html", "content": message_html}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def resolve_case_from_reply(db_conn, team_id: str, channel_id: str, reply_to_id: str):
    """Given an inbound Teams reply's (team_id, channel_id, reply_to_id),
    looks up the CaseThreadMap row so the caller can retrieve the matching
    Foundry thread id and continue that case's conversation with full
    context, per docs/teams-concurrency-design.md step 4.

    `db_conn` is any DB-API 2.0 compatible connection (pyodbc, etc.) already
    opened by the caller against the CaseThreadMap store.
    """
    cursor = db_conn.cursor()
    cursor.execute(
        """
        SELECT CaseId, FoundryThreadId
        FROM CaseThreadMap
        WHERE TeamId = ? AND ChannelId = ? AND RootMessageId = ?
        """,
        (team_id, channel_id, reply_to_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"case_id": row[0], "foundry_thread_id": row[1]}
