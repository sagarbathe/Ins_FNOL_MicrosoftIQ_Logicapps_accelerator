"""Gen 2-only creation and verification. No legacy environment or ID-file loading."""

import base64
import binascii
import hashlib
import json
import time
import uuid

import requests

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"


def create_approved_ontology(state, approved_sha256):
    from azure.identity import AzureCliCredential
    from v2.data import durable_write
    from v2.deploy import STATE, save

    fabric = state["fabric"]
    preview = fabric["ontologyPreview"]
    encoded = (STATE.parent / "ontology.definition.json").read_bytes()
    digest = hashlib.sha256(encoded).hexdigest()
    if not approved_sha256 or digest != approved_sha256 or digest != preview["sha256"]:
        raise ValueError("Explicit approval of the unchanged Gen 2 definition is required.")
    if fabric.get("verifiedCounts") != fabric.get("expectedCounts") or not fabric.get("verifiedCounts"):
        raise ValueError("Actual SQL row-count verification is required before ontology creation.")
    definition = json.loads(encoded)
    decode_gen2_definition(definition)
    client = FabricClient(AzureCliCredential(tenant_id=state["tenantId"]))
    workspace_id = str(uuid.UUID(fabric["workspaceId"]))
    workspace = client.request("GET", "/workspaces/" + workspace_id).json()
    marker = "fnol-delegated-v2:" + state["environmentId"]
    if (workspace.get("description") != marker
            or workspace.get("capacityId") != state["fabricCapacity"]["id"]):
        raise ValueError("The live workspace is not on the owned, approved Fabric capacity.")
    items = client.list_all(f"/workspaces/{workspace_id}/items?type=Ontology")
    matches = [item for item in items if item["displayName"] == preview["name"]]
    if len(matches) > 1 or (matches and matches[0].get("description") != marker):
        raise ValueError("Refusing to adopt an ambiguous or unowned ontology.")
    item = matches[0] if matches else durable_write(
        client, state, "ontology", f"/workspaces/{workspace_id}/ontologies",
        {"displayName": preview["name"], "description": marker, "definition": definition},
    )
    fabric["ontologyId"] = str(uuid.UUID(item["id"]))
    save(state)
    _, readback = client.verify_ontology(workspace_id, fabric["ontologyId"])
    decoded = decode_gen2_definition(readback)
    if set(decoded) != set(preview["parts"]):
        raise ValueError("The service definition parts do not match the approved Gen 2 preview.")
    from v2.ontology_definition import TABLE_SCHEMAS
    for table in TABLE_SCHEMAS:
        text = decoded[f"tables/{table}.tmdl"]
        if (f"ONT_WorkspaceId = {workspace_id}" not in text
                or f"ONT_ItemId = {fabric['lakehouseId']}" not in text):
            raise ValueError("The service definition does not retain the approved lakehouse bindings.")
    fabric["ontologyGenerationVerified"] = 2
    save(state)
    print("Ontology created and service generation 2/TMDL readback verified.")


def decode_gen2_definition(definition):
    parts = definition.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Missing ontology definition parts; Gen 2 cannot be verified.")
    decoded = {}
    for part in parts:
        path = part.get("path", "")
        if (path in decoded or "\\" in path or ".." in path.split("/")
                or path.startswith("/") or not path
                or (path != ".platform" and not path.endswith(".tmdl"))):
            raise ValueError(f"Not an unambiguous Gen 2 TMDL part: {path!r}")
        if part.get("payloadType") != "InlineBase64":
            raise ValueError(f"Unsupported payload encoding for {path!r}.")
        try:
            decoded[path] = base64.b64decode(
                part["payload"], validate=True,
            ).decode("utf-8")
        except (KeyError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise ValueError(f"Invalid TMDL payload for {path!r}.") from exc
    required = {".platform", "database.tmdl", "model.tmdl", "namespaces/default.tmdl"}
    if not required.issubset(decoded):
        raise ValueError(f"Missing Gen 2 readback parts: {sorted(required - decoded.keys())}")
    platform = json.loads(decoded[".platform"])
    if platform.get("metadata", {}).get("type") != "Ontology":
        raise ValueError("The definition is not an Ontology.")
    return decoded


def require_gen2(metadata, definition, *, workspace_id, ontology_id):
    """Fail closed: names and .platform config.version alone do not prove generation."""
    if (metadata.get("type") != "Ontology"
            or metadata.get("workspaceId") != workspace_id
            or metadata.get("id") != ontology_id):
        raise ValueError("Ontology readback does not match the requested deployment target.")
    generation = metadata.get("properties", {}).get("generation")
    if type(generation) is not int or generation != 2:
        raise ValueError(f"Expected ontology generation 2; service returned {generation!r}.")
    return decode_gen2_definition(definition)


class FabricClient:
    def __init__(self, credential):
        self.credential = credential

    def request(self, method, path, body=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("A Fabric API-relative path is required.")
        response = requests.request(
            method, FABRIC_API + path, json=body,
            headers={
                "Authorization": "Bearer " + self.credential.get_token(FABRIC_SCOPE).token,
                "x-ms-fabric-skill": "fabriciq-ontology-cli",
            },
            timeout=(15, 120), allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"Fabric {method} {path}: HTTP {response.status_code}: {response.text[:2000]}"
            )
        return response

    def complete(self, response, *, result=True):
        if response.status_code != 202:
            return response.json() if response.content else None
        operation_id = str(uuid.UUID(response.headers["x-ms-operation-id"]))
        operation_path = f"/operations/{operation_id}"
        print(f"Waiting for Fabric operation {operation_id}", flush=True)
        deadline = time.monotonic() + 1800
        retry_after = int(response.headers.get("Retry-After", "5"))
        while time.monotonic() < deadline:
            time.sleep(max(1, retry_after))
            poll = self.request("GET", operation_path)
            operation = poll.json()
            status = operation.get("status")
            if status == "Succeeded":
                return self.request("GET", operation_path + "/result").json() if result else None
            if status in ("Failed", "Cancelled"):
                raise RuntimeError(f"Fabric operation {operation_id}: {operation}")
            retry_after = int(poll.headers.get("Retry-After", "5"))
        raise TimeoutError(
            f"Fabric operation {operation_id} is still pending. Inspect it; do not repeat the write."
        )

    def list_all(self, path):
        records = []
        while path:
            data = self.request("GET", path).json()
            records.extend(data["value"])
            continuation = data.get("continuationUri")
            if continuation:
                if not continuation.startswith(FABRIC_API + "/"):
                    raise ValueError("Unexpected Fabric pagination endpoint.")
                path = continuation[len(FABRIC_API):]
            elif data.get("continuationToken"):
                raise ValueError("Fabric returned a continuation token without a continuation URI.")
            else:
                path = None
        return records

    def verify_ontology(self, workspace_id, ontology_id):
        base = f"/workspaces/{uuid.UUID(workspace_id)}/ontologies/{uuid.UUID(ontology_id)}"
        metadata = self.request("GET", base).json()
        response = self.request("POST", base + "/getDefinition", {})
        definition = self.complete(response)["definition"]
        require_gen2(
            metadata, definition, workspace_id=workspace_id, ontology_id=ontology_id,
        )
        return metadata, definition

    def create_gen2_ontology(self, *, workspace_id, display_name, description, definition):
        """Create only from TMDL; never return a usable ID before service readback passes."""
        workspace_id = str(uuid.UUID(workspace_id))
        decode_gen2_definition(definition)
        response = self.request(
            "POST", f"/workspaces/{workspace_id}/ontologies",
            {"displayName": display_name, "description": description, "definition": definition},
        )
        item = self.complete(response)
        ontology_id = str(uuid.UUID(item["id"]))
        return self.verify_ontology(workspace_id, ontology_id)
