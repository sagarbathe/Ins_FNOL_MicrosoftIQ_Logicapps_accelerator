"""
Creates a Fabric Ontology (RetailSalesOntology-style) on top of the LH_AutoFNOL lakehouse tables,
matching the microsoft-iq-solution-accelerator pattern: Lakehouse -> Ontology -> Data Agent.

Entities: Policyholder, Vehicle, Adjuster, RepairShop, Policy, Claim, FraudSignal, SubrogationFlag
Relationships: Claim->Policy, Claim->Vehicle, Claim->RepairShop, Claim->FraudSignal,
               Claim->SubrogationFlag, Policy->Policyholder, Policy->Vehicle (via PolicyVehicle),
               Claim->Adjuster (assignedToAdjuster, via Claim.AssignedAdjusterId)

Includes the Claim->Adjuster (assignedToAdjuster) relationship inline, so this single script
is the only step needed to build the full ontology (no separate add_adjuster_relationship.py
run is required).
"""
import base64
import json
import os
import random
import subprocess
import sys
import time
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

WORKSPACE_ID = config.FABRIC_WORKSPACE_ID
LAKEHOUSE_ID = config.FABRIC_LAKEHOUSE_ID
ONTOLOGY_NAME = config.FABRIC_ONTOLOGY_NAME

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


# ---------------------------------------------------------------------------
# Entity definitions: name -> {columns: {colName: valueType}, key: colName}
# valueType allowed: String, Boolean, DateTime, Object, BigInt, Double
# ---------------------------------------------------------------------------
ENTITY_SPECS = {
    "Policyholder": {
        "table": "Policyholder",
        "key": "PolicyholderId",
        "columns": {
            "PolicyholderId": "String", "Name": "String", "Email": "String",
            "Phone": "String", "State": "String", "TenureYears": "BigInt",
            "PriorClaimsCount": "BigInt",
        },
    },
    "Vehicle": {
        "table": "Vehicle",
        "key": "VehicleId",
        "columns": {
            "VehicleId": "String", "VIN": "String", "Make": "String", "Model": "String",
            "Year": "BigInt", "MarketValue": "Double", "TelematicsScore": "Double",
            "PriorDamageFlag": "Boolean",
        },
    },
    "Adjuster": {
        "table": "Adjuster",
        "key": "AdjusterId",
        "columns": {
            "AdjusterId": "String", "Name": "String", "Email": "String",
            "Specialty": "String", "Region": "String", "CurrentCaseload": "BigInt",
            "AvailabilityStatus": "String",
        },
    },
    "RepairShop": {
        "table": "RepairShop",
        "key": "ShopId",
        "columns": {
            "ShopId": "String", "Name": "String", "Network": "String",
            "Region": "String", "AvgCycleTimeDays": "Double",
        },
    },
    "Policy": {
        "table": "Policy",
        "key": "PolicyId",
        "columns": {
            "PolicyId": "String", "State": "String", "EffectiveDate": "String",
            "ExpirationDate": "String", "CoverageTypes": "String",
            "DeductibleCollision": "Double", "DeductibleComprehensive": "Double",
            "LiabilityLimitPerPerson": "Double", "LiabilityLimitPerAccident": "Double",
            "Endorsements": "String", "Status": "String",
        },
    },
    "Claim": {
        "table": "Claim",
        "key": "ClaimId",
        "columns": {
            "ClaimId": "String", "DateOfLoss": "String", "DateReported": "String",
            "ReportedChannel": "String", "LossType": "String", "LossDescription": "String",
            "Location": "String", "Severity": "String", "ReserveEstimate": "Double",
            "Status": "String", "FraudFlag": "Boolean", "SubrogationEligible": "Boolean",
            "AssignedAdjusterId": "String",
        },
    },
    "FraudSignal": {
        "table": "FraudSignal",
        "key": "ClaimId",
        "columns": {
            "ClaimId": "String", "SignalType": "String", "ScoreValue": "Double",
        },
    },
    "SubrogationFlag": {
        "table": "SubrogationFlag",
        "key": "ClaimId",
        "columns": {
            "ClaimId": "String", "AtFaultParty": "String",
            "ThirdPartyInsurer": "String", "RecoveryLikelihood": "String",
        },
    },
}

# Relationships: (name, source_entity, target_entity, join_table, join_schema,
#                 source_key_column_in_join, target_key_column_in_join)
RELATIONSHIPS = [
    ("relatesToPolicy", "Claim", "Policy", "Claim", "dbo", "ClaimId", "PolicyId"),
    ("involvesVehicle", "Claim", "Vehicle", "Claim", "dbo", "ClaimId", "VehicleId"),
    ("repairedAtShop", "Claim", "RepairShop", "Claim", "dbo", "ClaimId", "AssignedShopId"),
    ("hasFraudSignal", "Claim", "FraudSignal", "FraudSignal", "dbo", "ClaimId", "ClaimId"),
    ("hasSubrogationFlag", "Claim", "SubrogationFlag", "SubrogationFlag", "dbo", "ClaimId", "ClaimId"),
    ("belongsToPolicyholder", "Policy", "Policyholder", "Policy", "dbo", "PolicyId", "PolicyholderId"),
    ("coversVehicle", "Policy", "Vehicle", "PolicyVehicle", "dbo", "PolicyId", "VehicleId"),
    ("assignedToAdjuster", "Claim", "Adjuster", "Claim", "dbo", "ClaimId", "AssignedAdjusterId"),
]


def main():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Assign IDs
    entity_ids = {name: new_id() for name in ENTITY_SPECS}
    # property ids per entity: colname -> id
    property_ids = {}
    for name, spec in ENTITY_SPECS.items():
        property_ids[name] = {col: new_id() for col in spec["columns"]}

    parts = []

    # .platform
    parts.append({
        "path": ".platform",
        "payload": b64({"metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME}}),
        "payloadType": "InlineBase64",
    })
    # definition.json (empty)
    parts.append({"path": "definition.json", "payload": b64({}), "payloadType": "InlineBase64"})

    # EntityTypes
    for name, spec in ENTITY_SPECS.items():
        eid = entity_ids[name]
        key_prop_id = property_ids[name][spec["key"]]
        properties = [
            {
                "id": property_ids[name][col],
                "name": col,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": vtype,
            }
            for col, vtype in spec["columns"].items()
        ]
        entity_def = {
            "id": eid,
            "namespace": "usertypes",
            "baseEntityTypeId": None,
            "name": name,
            "entityIdParts": [key_prop_id],
            "displayNamePropertyId": key_prop_id,
            "namespaceType": "Custom",
            "visibility": "Visible",
            "properties": properties,
            "timeseriesProperties": [],
        }
        parts.append({
            "path": f"EntityTypes/{eid}/definition.json",
            "payload": b64(entity_def),
            "payloadType": "InlineBase64",
        })

        # DataBinding: bind every property straight from its own lakehouse table
        binding_id = new_id()
        # use a stable guid-looking id (docs use GUID format for binding filenames, but any unique string works based on samples)
        import uuid
        binding_guid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{name}-binding"))
        data_binding = {
            "id": binding_guid,
            "dataBindingConfiguration": {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": col, "targetPropertyId": property_ids[name][col]}
                    for col in spec["columns"]
                ],
                "sourceTableProperties": {
                    "sourceType": "LakehouseTable",
                    "workspaceId": WORKSPACE_ID,
                    "itemId": LAKEHOUSE_ID,
                    "sourceTableName": spec["table"],
                    "sourceSchema": "dbo",
                },
            },
        }
        parts.append({
            "path": f"EntityTypes/{eid}/DataBindings/{binding_guid}.json",
            "payload": b64(data_binding),
            "payloadType": "InlineBase64",
        })

    # RelationshipTypes + Contextualizations
    for rel_name, src_entity, tgt_entity, join_table, join_schema, src_join_col, tgt_join_col in RELATIONSHIPS:
        rel_id = new_id()
        rel_def = {
            "namespace": "usertypes",
            "id": rel_id,
            "name": rel_name,
            "namespaceType": "Custom",
            "source": {"entityTypeId": entity_ids[src_entity]},
            "target": {"entityTypeId": entity_ids[tgt_entity]},
        }
        parts.append({
            "path": f"RelationshipTypes/{rel_id}/definition.json",
            "payload": b64(rel_def),
            "payloadType": "InlineBase64",
        })

        import uuid
        ctx_guid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{rel_name}-ctx"))
        src_key_prop_id = property_ids[src_entity][ENTITY_SPECS[src_entity]["key"]]
        tgt_key_prop_id = property_ids[tgt_entity][ENTITY_SPECS[tgt_entity]["key"]]
        contextualization = {
            "id": ctx_guid,
            "dataBindingTable": {
                "sourceType": "LakehouseTable",
                "workspaceId": WORKSPACE_ID,
                "itemId": LAKEHOUSE_ID,
                "sourceTableName": join_table,
                "sourceSchema": join_schema,
            },
            "sourceKeyRefBindings": [
                {"sourceColumnName": src_join_col, "targetPropertyId": src_key_prop_id}
            ],
            "targetKeyRefBindings": [
                {"sourceColumnName": tgt_join_col, "targetPropertyId": tgt_key_prop_id}
            ],
        }
        parts.append({
            "path": f"RelationshipTypes/{rel_id}/Contextualizations/{ctx_guid}.json",
            "payload": b64(contextualization),
            "payloadType": "InlineBase64",
        })

    body = {"displayName": ONTOLOGY_NAME,
            "description": "Ontology over the Auto FNOL lakehouse: policyholders, policies, vehicles, "
                           "adjusters, repair shops, claims, fraud signals, and subrogation flags.",
            "definition": {"parts": parts}}

    url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/ontologies"
    resp = requests.post(url, headers=headers, json=body)
    print("createOntology:", resp.status_code)
    print(resp.text[:3000])

    if resp.status_code == 202:
        loc = resp.headers.get("Location")
        print("Polling:", loc)
        for _ in range(30):
            time.sleep(5)
            poll = requests.get(loc, headers=headers)
            data = poll.json()
            print("status:", data.get("status"))
            if data.get("status") in ("Succeeded", "Failed"):
                print(json.dumps(data, indent=2)[:3000])
                if data.get("status") == "Succeeded":
                    result = requests.get(loc + "/result", headers=headers)
                    print("Result:", result.status_code, result.text[:1000])
                    with open("ontology_id.txt", "w") as f:
                        try:
                            f.write(result.json().get("id", ""))
                        except Exception:
                            pass
                break
    elif resp.status_code == 201:
        data = resp.json()
        with open("ontology_id.txt", "w") as f:
            f.write(data.get("id", ""))
        print("Ontology ID:", data.get("id"))


if __name__ == "__main__":
    main()
