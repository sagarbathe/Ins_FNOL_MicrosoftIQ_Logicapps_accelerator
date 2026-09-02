"""
Public HTTPS wrapper (Flask, App Service Linux/B1) exposing the Work IQ
Graph-search helpers (tools_workiq_graph.py) as an OpenAPI-tool-callable
REST API for the Foundry orchestrator agent.

No VNet integration or Private Endpoint used or required - protected by a
shared API key header validated in-app (WORKIQ_API_KEY app setting) since
App Service's built-in auth is not required for this internal tool-calling
scenario.
"""
import os

from flask import Flask, jsonify, request

from tools_workiq_graph import search_documents, search_mail, send_email
from tools_fabric_iq import query_ontology
from post_to_teams import (
    post_new_case_alert,
    post_case_reply,
    resolve_case_from_reply,
    insert_case_thread_map,
    list_open_cases,
    mark_reply_processed,
    list_case_replies,
    _get_sql_connection,
)

app = Flask(__name__)

API_KEY = os.environ.get("WORKIQ_API_KEY", "")


def _check_api_key():
    if not API_KEY:
        return True  # no key configured -> open (dev only)
    return request.headers.get("x-api-key") == API_KEY


@app.route("/search-documents", methods=["POST"])
def search_documents_route():
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query", "")
    top = int(body.get("top", 5))
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        results = search_documents(query, top=top)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(results), 200


@app.route("/search-mail", methods=["POST"])
def search_mail_route():
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query", "")
    top = int(body.get("top", 5))
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        results = search_mail(query, top=top)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(results), 200


@app.route("/send-email", methods=["POST"])
def send_email_route():
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    to = body.get("to", "")
    subject = body.get("subject", "")
    body_text = body.get("bodyText", "")
    cc = body.get("cc", "")
    if not to or not subject or not body_text:
        return jsonify({"error": "to, subject, and bodyText are required"}), 400
    try:
        result = send_email(to, subject, body_text, cc=cc)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result), 200


@app.route("/query-ontology", methods=["POST"])
def query_ontology_route():
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    question = body.get("question", "")
    if not question:
        return jsonify({"error": "question is required"}), 400
    try:
        answer = query_ontology(question)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"answer": answer}), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/post-new-case-alert", methods=["POST"])
def post_new_case_alert_route():
    """Posts a NEW top-level Teams channel message (a case's reply-chain
    root) via the Agent Identity's delegated auth - required because Graph
    has no app-only permission for posting channel messages (see
    shared/post_to_teams.py docstring and
    docs/teams-concurrency-design.md step 1). Called by the Logic App
    trigger instead of a direct Managed-Identity Graph call, which cannot
    work for this operation.
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    message_html = body.get("messageHtml", "")
    if not team_id or not channel_id or not message_html:
        return jsonify({"error": "teamId, channelId, and messageHtml are required"}), 400
    try:
        message_id = post_new_case_alert(team_id, channel_id, message_html)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"id": message_id}), 200


@app.route("/post-case-reply", methods=["POST"])
def post_case_reply_route():
    """Posts a reply nested under an existing case's Teams root message
    (docs/teams-concurrency-design.md step 3), via Agent Identity auth.
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    root_message_id = body.get("rootMessageId", "")
    message_html = body.get("messageHtml", "")
    if not team_id or not channel_id or not root_message_id or not message_html:
        return jsonify({"error": "teamId, channelId, rootMessageId, and messageHtml are required"}), 400
    try:
        message_id = post_case_reply(team_id, channel_id, root_message_id, message_html)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"id": message_id}), 200


@app.route("/resolve-case-reply", methods=["POST"])
def resolve_case_reply_route():
    """Resolves an inbound Teams reply's (teamId, channelId, replyToId) back
    to its CaseId + FoundryThreadId via the CaseThreadMap table, per
    docs/teams-concurrency-design.md step 4 - lets a follow-up human message
    be routed back into the SAME Foundry thread/case, no matter how many
    other cases are concurrently open in the channel.
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    reply_to_id = body.get("replyToId", "")
    if not team_id or not channel_id or not reply_to_id:
        return jsonify({"error": "teamId, channelId, and replyToId are required"}), 400
    if not os.environ.get("CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING", ""):
        return jsonify({"error": "CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING is not configured"}), 500
    try:
        conn = _get_sql_connection()
        try:
            result = resolve_case_from_reply(conn, team_id, channel_id, reply_to_id)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if result is None:
        return jsonify({"error": "no matching case found"}), 404
    return jsonify(result), 200


@app.route("/insert-case-thread-map", methods=["POST"])
def insert_case_thread_map_route():
    """Idempotent insert of a new CaseThreadMap row (docs/teams-concurrency-design.md
    step 2 and step 5), via Agent Identity delegated SQL auth. Called by the
    Logic App right after minting a Foundry thread and posting the new
    case's Teams root message.
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    case_id = body.get("caseId", "")
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    root_message_id = body.get("rootMessageId", "")
    foundry_thread_id = body.get("foundryThreadId", "")
    if not all([case_id, team_id, channel_id, root_message_id, foundry_thread_id]):
        return jsonify({"error": "caseId, teamId, channelId, rootMessageId, and foundryThreadId are required"}), 400
    if not os.environ.get("CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING", ""):
        return jsonify({"error": "CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING is not configured"}), 500
    try:
        inserted = insert_case_thread_map(case_id, team_id, channel_id, root_message_id, foundry_thread_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"inserted": inserted}), 200


@app.route("/list-open-cases", methods=["POST"])
def list_open_cases_route():
    """Returns every tracked case (case_id, root_message_id,
    foundry_thread_id, last_processed_reply_id) for a given team/channel, so
    the reply-poller Logic App can enumerate each case's OWN
    "/messages/{root_message_id}/replies" Graph endpoint - the channel-level
    "/messages/delta" call never returns nested replies (see
    triggers/logicapp/workflow-teams-reply-poller.json _comment).
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    if not team_id or not channel_id:
        return jsonify({"error": "teamId and channelId are required"}), 400
    if not os.environ.get("CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING", ""):
        return jsonify({"error": "CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING is not configured"}), 500
    try:
        cases = list_open_cases(team_id, channel_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"cases": cases}), 200


@app.route("/list-case-replies", methods=["POST"])
def list_case_replies_route():
    """Lists Teams replies nested under a specific case's root message, via
    the Agent Identity's delegated Graph auth (post_to_teams.list_case_replies).
    Called by the reply-poller once per open case, per poll.
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    team_id = body.get("teamId", "")
    channel_id = body.get("channelId", "")
    root_message_id = body.get("rootMessageId", "")
    if not team_id or not channel_id or not root_message_id:
        return jsonify({"error": "teamId, channelId, and rootMessageId are required"}), 400
    try:
        replies = list_case_replies(team_id, channel_id, root_message_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"replies": replies}), 200


@app.route("/mark-reply-processed", methods=["POST"])
def mark_reply_processed_route():
    """Records the last Teams reply id processed for a case, so the poller
    never re-sends the same follow-up question to the Foundry thread on a
    subsequent poll (idempotency across the 30-second recurrence).
    """
    if not _check_api_key():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    case_id = body.get("caseId", "")
    reply_id = body.get("replyId", "")
    if not case_id or not reply_id:
        return jsonify({"error": "caseId and replyId are required"}), 400
    try:
        mark_reply_processed(case_id, reply_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
