"""
Adds the missing assignedToAdjuster relationship (Claim -> Adjuster) to the AutoFNOL_Ontology,
using updateDefinition to add a new RelationshipType + Contextualization part on top of the
existing ontology definition (does not touch existing entity/relationship parts).

NOTE: For NEW deployments, this is no longer needed as a separate step — create_ontology.py
now includes the Claim.AssignedAdjusterId property and the assignedToAdjuster relationship
directly. Keep this script only for patching an ontology that was created before that change.
"""
import base64
import json
import os
import subprocess
import sys
import time
import uuid
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

WORKSPACE_ID = config.FABRIC_WORKSPACE_ID
LAKEHOUSE_ID = config.FABRIC_LAKEHOUSE_ID
ONTOLOGY_ID = config.FABRIC_ONTOLOGY_ID

assert config.FABRIC_ONTOLOGY_ID, "Set FABRIC_ONTOLOGY_ID in .env after running fabric/create_ontology.py and copying fabric/ontology_id.txt"

# These must match the entity type IDs and key property IDs already created by create_ontology.py.
# Recovered by re-running the same deterministic ID generation (random.seed(42)) used there.
import random
random.seed(42)


def new_id():
    return str(random.getrandbits(60))


def get_token():
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        shell=True, text=True,
    )
    return out.strip()


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("utf-8")


token = get_token()
headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# Step 1: fetch current ontology definition to recover entity type IDs and
# key property IDs for Claim and Adjuster (rather than re-deriving via the
# random sequence, which is fragile if create_ontology.py's call order ever changes).
# ---------------------------------------------------------------------------
get_def_url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items/{ONTOLOGY_ID}/getDefinition"
resp = requests.post(get_def_url, headers=headers)
print("getDefinition:", resp.status_code)

if resp.status_code == 202:
    loc = resp.headers.get("Location")
    for _ in range(20):
        time.sleep(3)
        poll = requests.get(loc, headers=headers)
        data = poll.json()
        if data.get("status") in ("Succeeded", "Failed"):
            break
    result = requests.get(loc + "/result", headers=headers)
    definition = result.json()["definition"]
elif resp.status_code == 200:
    definition = resp.json()["definition"]
else:
    print(resp.text[:2000])
    raise SystemExit("Failed to get ontology definition")


def decode_part(part):
    return json.loads(base64.b64decode(part["payload"]).decode("utf-8"))


claim_entity_id = None
claim_key_prop_id = None
claim_adjuster_col_prop_id = None  # will need to add this property if missing
adjuster_entity_id = None
adjuster_key_prop_id = None

for part in definition["parts"]:
    if part["path"].startswith("EntityTypes/") and part["path"].endswith("/definition.json"):
        entity_def = decode_part(part)
        if entity_def["name"] == "Claim":
            claim_entity_id = entity_def["id"]
            claim_key_prop_id = entity_def["displayNamePropertyId"]
            claim_properties = entity_def["properties"]
        elif entity_def["name"] == "Adjuster":
            adjuster_entity_id = entity_def["id"]
            adjuster_key_prop_id = entity_def["displayNamePropertyId"]

print("Claim entity id:", claim_entity_id, "key prop:", claim_key_prop_id)
print("Adjuster entity id:", adjuster_entity_id, "key prop:", adjuster_key_prop_id)

if not claim_entity_id or not adjuster_entity_id:
    raise SystemExit("Could not locate Claim/Adjuster entity types in existing ontology definition")

# ---------------------------------------------------------------------------
# Step 2: add a new property "AssignedAdjusterId" to the Claim entity type
# (the Claim table now has this column; the entity type must expose it before
# a relationship/contextualization can bind to it).
# ---------------------------------------------------------------------------
new_prop_id = new_id()
already_has_prop = any(p["name"] == "AssignedAdjusterId" for p in claim_properties)
if not already_has_prop:
    claim_properties.append({
        "id": new_prop_id,
        "name": "AssignedAdjusterId",
        "redefines": None,
        "baseTypeNamespaceType": None,
        "valueType": "String",
    })
else:
    new_prop_id = next(p["id"] for p in claim_properties if p["name"] == "AssignedAdjusterId")

updated_claim_def = None
updated_claim_binding = None
new_parts = []

for part in definition["parts"]:
    if part["path"] == f"EntityTypes/{claim_entity_id}/definition.json":
        entity_def = decode_part(part)
        entity_def["properties"] = claim_properties
        updated_claim_def = {
            "path": part["path"],
            "payload": b64(entity_def),
            "payloadType": "InlineBase64",
        }
    elif part["path"].startswith(f"EntityTypes/{claim_entity_id}/DataBindings/"):
        binding = decode_part(part)
        if not already_has_prop:
            binding["dataBindingConfiguration"]["propertyBindings"].append({
                "sourceColumnName": "AssignedAdjusterId",
                "targetPropertyId": new_prop_id,
            })
        updated_claim_binding = {
            "path": part["path"],
            "payload": b64(binding),
            "payloadType": "InlineBase64",
        }

# Rebuild the full parts list, substituting the updated Claim definition/binding parts.
final_parts = []
for part in definition["parts"]:
    if part["path"] == f"EntityTypes/{claim_entity_id}/definition.json":
        final_parts.append(updated_claim_def)
    elif part["path"].startswith(f"EntityTypes/{claim_entity_id}/DataBindings/"):
        final_parts.append(updated_claim_binding)
    else:
        final_parts.append(part)

# ---------------------------------------------------------------------------
# Step 3: add the new RelationshipType + Contextualization: Claim -assignedToAdjuster-> Adjuster
# ---------------------------------------------------------------------------
rel_id = new_id()
rel_def = {
    "namespace": "usertypes",
    "id": rel_id,
    "name": "assignedToAdjuster",
    "namespaceType": "Custom",
    "source": {"entityTypeId": claim_entity_id},
    "target": {"entityTypeId": adjuster_entity_id},
}
final_parts.append({
    "path": f"RelationshipTypes/{rel_id}/definition.json",
    "payload": b64(rel_def),
    "payloadType": "InlineBase64",
})

ctx_guid = str(uuid.uuid5(uuid.NAMESPACE_OID, "assignedToAdjuster-ctx"))
contextualization = {
    "id": ctx_guid,
    "dataBindingTable": {
        "sourceType": "LakehouseTable",
        "workspaceId": WORKSPACE_ID,
        "itemId": LAKEHOUSE_ID,
        "sourceTableName": "Claim",
        "sourceSchema": "dbo",
    },
    "sourceKeyRefBindings": [
        {"sourceColumnName": "ClaimId", "targetPropertyId": claim_key_prop_id}
    ],
    "targetKeyRefBindings": [
        {"sourceColumnName": "AssignedAdjusterId", "targetPropertyId": adjuster_key_prop_id}
    ],
}
final_parts.append({
    "path": f"RelationshipTypes/{rel_id}/Contextualizations/{ctx_guid}.json",
    "payload": b64(contextualization),
    "payloadType": "InlineBase64",
})

body = {"definition": {"parts": final_parts}}

update_url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items/{ONTOLOGY_ID}/updateDefinition"
resp = requests.post(update_url, headers=headers, json=body)
print("updateDefinition (ontology):", resp.status_code)
print(resp.text[:2000])

if resp.status_code == 202:
    loc = resp.headers.get("Location")
    for _ in range(30):
        time.sleep(5)
        poll = requests.get(loc, headers=headers)
        data = poll.json()
        print("status:", data.get("status"))
        if data.get("status") in ("Succeeded", "Failed"):
            print(json.dumps(data, indent=2)[:3000])
            break

with open("adjuster_relationship_id.txt", "w") as f:
    f.write(rel_id)
print("New relationship type id:", rel_id)
print("New property id (Claim.AssignedAdjusterId):", new_prop_id)
