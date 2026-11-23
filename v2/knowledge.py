"""Permission-aware synthetic knowledge on the isolated Search service."""

import hashlib
import re
import time
import uuid

import requests

from v2.deploy import ROOT, save
from v2.provision import Rest, clients

API_VERSION = "2026-10-01"
BANNER = "ILLUSTRATIVE SYNTHETIC PILOT CONTENT. Not legal advice or authoritative regulatory guidance. "


def build_chunks(path, allowed_users):
    if not allowed_users:
        raise ValueError(f"No explicit grants for {path.name}.")
    users = [str(uuid.UUID(user)) for user in allowed_users]
    text = path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    words = text.split()
    result = []
    for offset in range(0, len(words), 250):
        identifier = hashlib.sha256(f"{path.name}:{offset}".encode()).hexdigest()
        result.append({
            "id": identifier, "title": title,
            "content": BANNER + " ".join(words[offset:offset + 280]),
            "source": path.name, "UserIds": users,
        })
    if not result:
        raise ValueError(f"Empty knowledge document: {path.name}.")
    return result


def provision_knowledge(state):
    credential, arm, _ = clients(state)
    azure = state["azure"]
    service = arm.call("GET", azure["searchId"] + "?api-version=2025-05-01")
    if service.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise RuntimeError("Only the owned v2 Search service may be provisioned.")
    search = Rest(credential, azure["searchUrl"], "https://search.azure.com/.default")
    index_name, source_name, base_name = "fnol-v2-knowledge", "fnol-v2-source", "fnol-v2-kb"
    documents = sorted((ROOT / "v2" / "knowledge_docs").glob("*.md"))
    if len(documents) != 5:
        raise RuntimeError("Expected the five explicit illustrative FNOL knowledge documents.")
    if "knowledgeGrants" not in state:
        state["knowledgeGrants"] = {
            "policy": "synthetic-pilot-owner-only",
            "documents": {path.name: [state["operatorId"]] for path in documents},
        }
        save(state)
    grants = state["knowledgeGrants"]["documents"]
    chunks = [chunk for path in documents for chunk in build_chunks(path, grants.get(path.name, []))]
    hidden_id = "acl-boundary-control"
    chunks.append({
        "id": hidden_id, "title": "ACL boundary control", "source": "synthetic-access-control-test",
        "content": BANNER + "This document has no grants and must not be returned.", "UserIds": [],
    })
    index_path = f"/indexes/{index_name}?api-version={API_VERSION}"
    search.call("PUT", index_path, {
        "name": index_name,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
            {"name": "source", "type": "Edm.String", "filterable": True},
            {"name": "UserIds", "type": "Collection(Edm.String)", "filterable": True,
             "retrievable": False, "permissionFilter": "userIds"},
        ],
        "permissionFilterOption": "enabled",
        "semantic": {"configurations": [{
            "name": "fnol-semantic", "prioritizedFields": {
                "titleField": {"fieldName": "title"},
                "prioritizedContentFields": [{"fieldName": "content"}],
            },
        }]},
    })
    schema = search.call("GET", index_path)
    acl = [field for field in schema["fields"] if field.get("permissionFilter") == "userIds"]
    if schema.get("permissionFilterOption") != "enabled" or len(acl) != 1 or acl[0]["name"] != "UserIds":
        raise RuntimeError("Native ACL index configuration was not persisted.")
    uploaded = search.call(
        "POST", f"/indexes/{index_name}/docs/index?api-version={API_VERSION}",
        {"value": [{"@search.action": "upload", **chunk} for chunk in chunks]},
    )
    if len(uploaded["value"]) != len(chunks) or not all(item["status"] for item in uploaded["value"]):
        raise RuntimeError("One or more knowledge chunks failed to index.")
    source_path = f"/knowledgesources/{source_name}?api-version={API_VERSION}"
    search.call("PUT", source_path, {
        "name": source_name, "kind": "searchIndex",
        "description": "Illustrative FNOL knowledge; owner-only synthetic pilot grants.",
        "searchIndexParameters": {
            "searchIndexName": index_name, "semanticConfigurationName": "fnol-semantic",
            "searchFields": [{"name": "title"}, {"name": "content"}],
            "sourceDataFields": [{"name": field} for field in ("id", "title", "content", "source")],
        },
    })
    base_path = f"/knowledgebases/{base_name}?api-version={API_VERSION}"
    search.call("PUT", base_path, {
        "name": base_name, "description": "Isolated ACL-protected illustrative FNOL pilot.",
        "knowledgeSources": [{"name": source_name}],
        "retrievalReasoningEffort": {"kind": "minimal"}, "outputMode": "extractiveData",
    })
    source = search.call("GET", source_path)
    base = search.call("GET", base_path)
    if (source["searchIndexParameters"]["searchIndexName"] != index_name
            or base["knowledgeSources"] != [{"name": source_name}]):
        raise RuntimeError("Knowledge source/base readback does not match the v2 index.")
    token = credential.get_token("https://search.azure.com/.default").token
    expected_ids = {chunk["id"] for chunk in chunks if state["operatorId"] in chunk["UserIds"]}
    verified = False
    for _ in range(12):
        response = requests.post(
            azure["searchUrl"] + f"/indexes/{index_name}/docs/search?api-version={API_VERSION}",
            headers={"Authorization": "Bearer " + token, "x-ms-query-source-authorization": token},
            json={"search": "*", "top": 1000, "select": "id"},
            timeout=(15, 60), allow_redirects=False,
        )
        response.raise_for_status()
        returned = {item["id"] for item in response.json()["value"]}
        if hidden_id in returned or not returned <= expected_ids:
            raise RuntimeError("A document without the delegated user's grant was returned.")
        if returned == expected_ids:
            verified = True
            break
        time.sleep(5)
    if not verified:
        raise RuntimeError("The indexed documents did not become available for the delegated ACL test.")
    connection_name = "fnol-v2-knowledge"
    target = azure["searchUrl"] + f"/knowledgebases/{base_name}/mcp?api-version=2026-11-01-preview"
    connection = arm.call(
        "PUT", azure["projectId"] + f"/connections/{connection_name}?api-version=2025-10-01-preview",
        {"name": connection_name, "properties": {
            "authType": "ProjectManagedIdentity", "category": "RemoteTool", "target": target,
            "isSharedToAll": True, "audience": "https://search.azure.com/", "metadata": {"ApiType": "Azure"},
        }},
    )
    state["knowledge"] = {
        "index": index_name, "source": source_name, "base": base_name,
        "connectionId": connection["id"], "mcpEndpoint": target,
        "documentCount": len(documents), "chunkCount": len(chunks),
        "operatorAclVerified": True, "twoUserAcceptanceVerified": False,
    }
    save(state)
    print("ACL-protected knowledge source/base created; operator allow and no-grant denial verified.")
