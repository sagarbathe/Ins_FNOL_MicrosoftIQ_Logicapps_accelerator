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

from tools_workiq_graph import search_documents, search_mail
from tools_fabric_iq import query_ontology

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
    mailbox_user_id = body.get("mailboxUserId", "")
    top = int(body.get("top", 5))
    if not query or not mailbox_user_id:
        return jsonify({"error": "query and mailboxUserId are required"}), 400
    try:
        results = search_mail(query, mailbox_user_id, top=top)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(results), 200


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
