"""
Work IQ tool implementation for the Foundry orchestrator agent — a thin
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

import requests
from msal import ConfidentialClientApplication

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _bearer_header(token: str) -> str:
    return "Bearer " + token


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


def search_documents(query: str, top: int = 5) -> list[dict]:
    """SharePoint/OneDrive document search via Graph Search API.

    Provides Work IQ-equivalent document-search capability for the
    orchestrator agent.
    """
    token = _get_graph_token()
    body = {
        "requests": [
            {
                "entityTypes": ["driveItem"],
                "query": {"queryString": query},
                "from": 0,
                "size": top,
                # Required for app-only (application permission) Search API
                # calls - any valid Azure geo works (does not need to match
                # tenant region exactly).
                "region": os.environ.get("GRAPH_SEARCH_REGION", "NAM"),
            }
        ]
    }
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


def search_mail(query: str, mailbox_user_id: str, top: int = 5) -> list[dict]:
    """Outlook mail search via Graph, scoped to a specific mailbox
    (the same mailbox the Logic App trigger monitors, or another shared
    mailbox as configured).

    Provides Work IQ-equivalent mail-search capability for the orchestrator
    agent.
    """
    token = _get_graph_token()
    resp = requests.get(
        f"{GRAPH_BASE}/users/{mailbox_user_id}/messages",
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
