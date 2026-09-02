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
import sys
import requests
import markdown
from msal import ConfidentialClientApplication

sys.path.append(os.path.dirname(__file__))

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _markdown_to_teams_html(text: str) -> str:
    """Converts the Foundry agent's Markdown response (headings, **bold**,
    "- " bullet lists, blank-line paragraphs, tables, emojis) into real HTML
    so Teams renders proper bold text, indented/nested bullet lists,
    paragraph spacing, and tables instead of literal '**' / '-' characters
    and run-on text. Teams channel messages support a restricted HTML subset
    (p, br, strong, em, ul, ol, li, h1-h3, a, blockquote, table, span with a
    "style" color/background attribute) which the "extra" + "sane_lists" +
    "nl2br" markdown extensions map well onto.

    Also recolors the leading urgency-badge emoji (🔴/🟠/🟢) inline so the
    urgency level pops visually in the Teams channel (not just the emoji
    itself, but the whole "Urgency Assessment" line takes on that color).
    """
    if not text:
        return text
    html = markdown.markdown(text, extensions=["extra", "sane_lists", "nl2br"])
    html = _colorize_urgency_line(html)
    return _add_block_spacing(html)


_URGENCY_COLORS = {
    "🔴": "#C0392B",  # red - urgent/high severity
    "🟠": "#D68910",  # amber - elevated/needs prompt review
    "🟢": "#1E8449",  # green - routine/low severity
}


def _colorize_urgency_line(html: str) -> str:
    """Wraps a paragraph/list-item that begins with one of the urgency
    emojis (🔴/🟠/🟢) in a colored <span>, so the "Urgency Assessment" line
    from ORCHESTRATOR_INSTRUCTIONS visually stands out in the Teams
    channel message instead of being plain black text like everything else.
    """
    import re

    for emoji, color in _URGENCY_COLORS.items():
        pattern = re.compile(r"(<p>)(\s*" + re.escape(emoji) + r".*?)(</p>)", re.DOTALL)
        html = pattern.sub(
            lambda m, c=color: f'{m.group(1)}<span style="color:{c}">{m.group(2)}</span>{m.group(3)}',
            html,
        )
    return html


def _add_block_spacing(html: str) -> str:
    """Teams' channel-message HTML renderer ignores normal browser default
    margins on <p>/<ul>/<ol>/<h1-3>/<blockquote>/<table> (they render nearly
    flush against each other), so even when the agent's markdown correctly
    inserts blank lines between sections/topics, the rendered message still
    looks like one dense block with no visual separation. Force real vertical
    spacing by inline-styling every top-level block element with a margin,
    since Teams strips <style> blocks/classes and only honors inline "style"
    attributes on a restricted HTML subset.
    """
    import re

    replacements = [
        (re.compile(r"<p(?![^>]*style=)([^>]*)>"), '<p\\1 style="margin:0 0 12px 0;">'),
        (re.compile(r"<h([1-3])(?![^>]*style=)([^>]*)>"), '<h\\1\\2 style="margin:16px 0 8px 0;">'),
        (re.compile(r"<ul(?![^>]*style=)([^>]*)>"), '<ul\\1 style="margin:0 0 12px 0; padding-left:20px;">'),
        (re.compile(r"<ol(?![^>]*style=)([^>]*)>"), '<ol\\1 style="margin:0 0 12px 0; padding-left:20px;">'),
        (re.compile(r"<li(?![^>]*style=)([^>]*)>"), '<li\\1 style="margin:0 0 4px 0;">'),
        (re.compile(r"<blockquote(?![^>]*style=)([^>]*)>"), '<blockquote\\1 style="margin:0 0 12px 0;">'),
        (re.compile(r"<table(?![^>]*style=)([^>]*)>"), '<table\\1 style="margin:8px 0 12px 0; border-collapse:collapse;">'),
    ]
    for pattern, repl in replacements:
        html = pattern.sub(repl, html)
    return html


def _get_graph_token() -> str:
    """AUTH_MODE=agent_identity (default whenever AGENT_IDENTITY_TENANT_ID is
    set) posts to Teams as the dedicated Agent Identity's own delegated
    identity (it's a real member of the team/channel) instead of app-only.
    This is required, not just preferred: Microsoft Graph has no
    application-permission role for posting channel messages
    (ChannelMessage.Send does not exist as an app-only permission - only
    ChannelMessage.Read.All and ChannelMessage.UpdatePolicyViolation.All do),
    so the previous app-only approach here could never have worked - see
    FNOL_Logicapps_Solution_Status.docx section 4.6.
    """
    auth_mode = os.environ.get(
        "AUTH_MODE", "agent_identity" if os.environ.get("AGENT_IDENTITY_TENANT_ID") else "service_principal"
    )
    if auth_mode == "agent_identity":
        from agent_identity_auth import GRAPH_SCOPE as AGENT_GRAPH_SCOPE, get_agent_token

        return get_agent_token(AGENT_GRAPH_SCOPE)

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
        json={"body": {"contentType": "html", "content": _markdown_to_teams_html(message_html)}},
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
        json={"body": {"contentType": "html", "content": _markdown_to_teams_html(message_html)}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def list_case_replies(team_id: str, channel_id: str, root_message_id: str) -> list:
    """Lists all replies nested under a specific case's root message via
    Graph's "/messages/{root_message_id}/replies" endpoint.

    This is the ONLY Graph endpoint that returns nested replies - the
    channel-level "/messages/delta" call used to discover NEW top-level case
    threads never includes replies, so the reply-poller must call this
    per-case endpoint for every open case on each poll.
    """
    token = _get_graph_token()
    url = (
        f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}"
        f"/messages/{root_message_id}/replies"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def _get_sql_connection():
    """Opens a pyodbc connection to the CaseThreadMap Fabric SQL Database
    using the Agent Identity's delegated database.windows.net token (Entra
    ID auth, no SQL login/password stored anywhere).
    """
    import os
    import struct

    import pyodbc

    conn_str = os.environ["CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING"]
    from agent_identity_auth import get_agent_token

    token = get_agent_token(["https://database.windows.net/.default"])
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack("=i", len(token_bytes)) + token_bytes
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}, timeout=10)


def insert_case_thread_map(case_id: str, team_id: str, channel_id: str, root_message_id: str, foundry_thread_id: str) -> bool:
    """Idempotent insert into CaseThreadMap keyed by CaseId (PK) - see
    docs/teams-concurrency-design.md step 5 and
    shared/case_thread_store_schema.sql. Returns True if a new row was
    inserted, False if the CaseId already existed (duplicate/retry - safe
    no-op).
    """
    conn = _get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM CaseThreadMap WHERE CaseId = ?", (case_id,))
        if cursor.fetchone() is not None:
            return False
        cursor.execute(
            """
            INSERT INTO CaseThreadMap (CaseId, TeamId, ChannelId, RootMessageId, FoundryThreadId)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, team_id, channel_id, root_message_id, foundry_thread_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


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


def list_open_cases(team_id: str, channel_id: str):
    """Returns every tracked case's (case_id, root_message_id,
    foundry_thread_id, last_processed_reply_id) for the given Teams
    team/channel, so the reply-poller Logic App can enumerate each case's
    OWN "/messages/{root_message_id}/replies" Graph endpoint - Microsoft
    Graph's channel-level "/messages/delta" call only returns top-level
    messages and never includes nested replies, so per-case polling is
    required to ever see follow-up questions.
    """
    conn = _get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT CaseId, RootMessageId, FoundryThreadId, LastProcessedReplyId
            FROM CaseThreadMap
            WHERE TeamId = ? AND ChannelId = ?
            """,
            (team_id, channel_id),
        )
        rows = cursor.fetchall()
        return [
            {
                "case_id": r[0],
                "root_message_id": r[1],
                "foundry_thread_id": r[2],
                "last_processed_reply_id": r[3],
            }
            for r in rows
        ]
    finally:
        conn.close()


def mark_reply_processed(case_id: str, reply_id: str) -> None:
    """Records the last Teams reply id processed for this case, so the
    poller never re-sends the same follow-up question to the Foundry
    thread on a subsequent poll (idempotency across the 30-second
    recurrence).
    """
    conn = _get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE CaseThreadMap
            SET LastProcessedReplyId = ?, LastProcessedReplyUtc = SYSUTCDATETIME(), LastActivityUtc = SYSUTCDATETIME()
            WHERE CaseId = ?
            """,
            (reply_id, case_id),
        )
        conn.commit()
    finally:
        conn.close()
