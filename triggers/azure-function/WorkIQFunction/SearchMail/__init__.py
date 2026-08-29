"""
HTTP-triggered Azure Function wrapping tools_workiq_graph.search_mail()
for the Foundry orchestrator agent's Work IQ OpenAPI tool.

Public HTTPS endpoint (Consumption plan, function-key auth) - no VNet
integration or Private Endpoint used or required.
"""
import json
import logging
import os
import sys

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from tools_workiq_graph import search_mail  # noqa: E402


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    query = body.get("query", "")
    mailbox_user_id = body.get("mailboxUserId", "")
    top = int(body.get("top", 5))

    if not query or not mailbox_user_id:
        return func.HttpResponse(
            json.dumps({"error": "query and mailboxUserId are required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        results = search_mail(query, mailbox_user_id, top=top)
    except Exception as e:
        logging.exception("search_mail failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )

    return func.HttpResponse(json.dumps(results), status_code=200, mimetype="application/json")
