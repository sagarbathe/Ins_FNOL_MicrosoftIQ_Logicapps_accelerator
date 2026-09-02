"""
Work IQ tool implementation for the Foundry orchestrator agent â€” a thin
wrapper over Microsoft Graph used by the "workiq_graph_search" OpenAPI tool
referenced in create_orchestrator_agent.py.

This intentionally uses PUBLIC Microsoft Graph endpoints only
(graph.microsoft.com) with Entra ID app-only auth - no VNet integration,
no Private Endpoints, no network isolation components are used or required
for this tool to function, by design for this accelerator.

Two search capabilities, providing Work IQ-equivalent search for the
orchestrator agent:

  search_documents(query)  -> SharePoint/OneDrive document search
                                (CAT bulletins, ad hoc ops guidance, etc.)
  search_mail(query)        -> Outlook mail search (SIU routing change
                                emails, etc.)

Default implementation uses the general-availability Graph Search API
(/search/query) and Graph mail $search - see docs/design-options.md
section A.2 for the tradeoff vs. the (possibly-preview-only) M365 Copilot
Retrieval API, which can be swapped in later by changing only the
`_call_documents_backend` / `_call_mail_backend` functions below without
touching the OpenAPI contract the Foundry agent calls.
"""
import os
import sys

import requests
from msal import ConfidentialClientApplication

sys.path.append(os.path.dirname(__file__))

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _bearer_header(token: str) -> str:
    return "Bearer " + token


def _get_graph_token() -> str:
    """AUTH_MODE=agent_identity (default whenever AGENT_IDENTITY_TENANT_ID
    is set) acquires a delegated token directly as the dedicated Agent
    Identity (its own mailbox + Teams membership) instead of an app-only
    service-principal token - see shared/agent_identity_auth.py and
    docs/OBO_Bot_Teams_Design.docx.
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


def search_documents(query: str, top: int = 5) -> list[dict]:
    """SharePoint/OneDrive document search via Graph Search API.

    Provides Work IQ-equivalent document-search capability for the
    orchestrator agent.
    """
    token = _get_graph_token()
    auth_mode = os.environ.get(
        "AUTH_MODE", "agent_identity" if os.environ.get("AGENT_IDENTITY_TENANT_ID") else "service_principal"
    )
    request_spec = {
        "entityTypes": ["driveItem"],
        "query": {"queryString": query},
        "from": 0,
        "size": top,
    }
    if auth_mode != "agent_identity":
        # "region" is required for app-only (application permission) Search
        # API calls, but is REJECTED ("Region is not supported when request
        # with delegated permission.") for a delegated/agent-identity token -
        # only set it in service-principal auth mode.
        request_spec["region"] = os.environ.get("GRAPH_SEARCH_REGION", "NAM")
    body = {"requests": [request_spec]}
    resp = requests.post(
        f"{GRAPH_BASE}/search/query",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    hits = []
    for container in resp.json().get("value", []):
        for hits_container in container.get("hitsContainers", []):
            for hit in hits_container.get("hits", []):
                res = hit.get("resource", {})
                hits.append(
                    {
                        "name": res.get("name"),
                        "webUrl": res.get("webUrl"),
                        "summary": hit.get("summary"),
                        "lastModified": res.get("lastModifiedDateTime"),
                    }
                )
    return hits


def search_mail(query: str, top: int = 5) -> list[dict]:
    """Outlook mail search via Graph, ALWAYS scoped to the Agent Identity's
    own mailbox (the same mailbox the Logic App trigger monitors) via the
    delegated token's own "/me/messages" endpoint.

    The mailbox is intentionally NOT an LLM-supplied parameter. Previously
    this took a free-text `mailboxUserId` argument that the orchestrator
    agent had to guess at call time, which led it to hallucinate a
    plausible-looking but nonexistent address (e.g.
    "fnolagent@contosoinsurance.com") instead of the real Agent Identity
    UPN - causing the downstream Graph call to fail. Using "/me/messages"
    removes that failure mode entirely: there is only ever one mailbox this
    tool can search (the Agent Identity's own), so there is nothing left
    for the model to get wrong.

    Provides Work IQ-equivalent mail-search capability for the orchestrator
    agent.
    """
    token = _get_graph_token()
    resp = requests.get(
        f"{GRAPH_BASE}/me/messages",
        headers={"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"},
        params={"$search": f'"{query}"', "$top": top},
        timeout=30,
    )
    resp.raise_for_status()
    hits = []
    for msg in resp.json().get("value", []):
        hits.append(
            {
                "subject": msg.get("subject"),
                "from": (msg.get("from", {}).get("emailAddress", {}) or {}).get("address"),
                "receivedDateTime": msg.get("receivedDateTime"),
                "bodyPreview": msg.get("bodyPreview"),
                "webLink": msg.get("webLink"),
            }
        )
    return hits


def send_email(to: str, subject: str, body_text: str, cc: str = "") -> dict:
    """Send an email from the Agent Identity's own mailbox via Graph
    /me/sendMail, so the FNOL triage agent can escalate a case (e.g. to
    SIU or an adjuster) directly from a Teams follow-up. Uses the same
    delegated Agent Identity token as search_mail/search_documents - no
    separate app registration or auth mode needed. Requires the
    Mail.Send delegated Graph scope (see shared/agent_identity_auth.py).
    """
    token = _get_graph_token()
    to_recipients = [{"emailAddress": {"address": addr.strip()}} for addr in to.split(",") if addr.strip()]
    cc_recipients = [{"emailAddress": {"address": addr.strip()}} for addr in cc.split(",") if addr.strip()]
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": to_recipients,
    }
    if cc_recipients:
        message["ccRecipients"] = cc_recipients
    resp = requests.post(
        f"{GRAPH_BASE}/me/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "saveToSentItems": True},
        timeout=30,
    )
    resp.raise_for_status()
    return {"status": "sent", "to": to, "subject": subject}


# --- Retrieval-API variant (swap in when available in your tenant) --------
def search_documents_via_copilot_retrieval_api(query: str, top: int = 5) -> list[dict]:
    """Same-index equivalent using the M365 Copilot Retrieval API
    (/copilot/retrieval), when enabled for your tenant/license. Kept as a
    separate function so callers can switch backends without changing the
    OpenAPI tool contract. Not wired in by default - see design-options.md.
    """
    token = _get_graph_token()
    body = {"queryString": query, "dataSource": "sharePoint", "maximumNumberOfResults": top}
    resp = requests.post(
        f"{GRAPH_BASE}/copilot/retrieval",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("retrievalHits", [])
