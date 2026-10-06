"""
Creates a Generation 2 ("new experience", TMDL-based) Fabric Ontology for Auto FNOL, on top of
the SAME LH_AutoFNOL lakehouse tables used by the Gen 1 ontology (fabric/create_ontology.py).

Reference docs:
- Create Ontology (Gen 2 / public definition):
  https://learn.microsoft.com/en-us/rest/api/fabric/ontology/items/create-ontology
- Ontology (new) item definition (TMDL format):
  https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition

Entities (same model as the Gen 1 ontology): Policyholder, Vehicle, Adjuster, RepairShop,
Policy, Claim, FraudSignal, SubrogationFlag.

Backing tables: the 8 entity tables above, plus the PolicyVehicle junction table (used only to
back the many-to-many coversVehicle entity relationship, not an entity itself).

Relationships (TOM `relationship` objects in relationships.tmdl, matching the Gen 1
RELATIONSHIPS list exactly) -> ontology-level `entityRelationship` objects in
entityRelationships.tmdl that bind to them:
  relatesToPolicy        Claim.PolicyId         -> Policy.PolicyId
  involvesVehicle        Claim.VehicleId        -> Vehicle.VehicleId
  repairedAtShop         Claim.AssignedShopId   -> RepairShop.ShopId
  assignedToAdjuster     Claim.AssignedAdjusterId -> Adjuster.AdjusterId
  hasFraudSignal         FraudSignal.ClaimId    -> Claim.ClaimId
  hasSubrogationFlag     SubrogationFlag.ClaimId -> Claim.ClaimId
  belongsToPolicyholder  Policy.PolicyholderId  -> Policyholder.PolicyholderId
  coversVehicle          PolicyVehicle (junction table: PolicyId -> Policy, VehicleId -> Vehicle)

How a new-experience (Gen 2) ontology differs from the Gen 1 script:
- The definition is TMDL text (plus a .platform JSON file), not entity-type JSON.
- Generation is NOT an explicit field: it is inferred from which definition-part format is
  used. Supplying TMDL parts (database.tmdl, tables/*.tmdl, entities/*.tmdl, ...) resolves to
  Generation 2.
- Backing tables bind to the lakehouse via a DirectLake-over-SQL `partition` + a shared
  `Sql.Database(<sql-endpoint>, <sql-endpoint-id>)` M expression, instead of the Gen 1
  `LakehouseTable` DataBinding (workspaceId/itemId) shape. The SQL analytics endpoint
  connection string + id are read from the lakehouse's own properties at run time.

IMPORTANT: Keep this script alongside fabric/create_ontology.py (Gen 1). Fabric Data Agents
do not yet support Gen 2 ("new experience") ontologies, so both versions must be maintained
until that support lands — Gen 1 remains the one wired into the Data Agent, Gen 2 is used for
the new-experience portal features (entity Instances, graph model, etc.).

Validated working: table partitions must include ONT_WorkspaceId / ONT_ItemId / ONT_ItemKind /
ONT_ItemName annotations (see build_table()) so the Fabric portal can resolve which Fabric item
backs each table's data. Without these, entity "Instances" show "The kind of Fabric item this
data source points to couldn't be identified." This was confirmed as the root cause by isolated
testing (adding only the annotations, with no other changes, fixed the Instances binding).
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
ONTOLOGY_NAME = config.FABRIC_ONTOLOGY_V2_NAME

TAB = "\t"


def new_tag():
    return str(uuid.uuid4())


def get_token():
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        shell=True, text=True,
    )
    return out.strip()


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def get_lakehouse_sql_endpoint(headers):
    """Reads the SQL analytics endpoint connection string + database id off the existing
    LH_AutoFNOL lakehouse, so the Gen 2 ontology's DirectLake partitions point at the same
    lakehouse the Gen 1 ontology already uses. Also returns the lakehouse's displayName,
    needed for the ONT_ItemName partition annotation."""
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/lakehouses/{LAKEHOUSE_ID}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    props = body.get("properties", {})
    lakehouse_name = body.get("displayName")
    sql_endpoint = props.get("sqlEndpointProperties", {})
    connection_string = sql_endpoint.get("connectionString")
    database_id = sql_endpoint.get("id")
    if not connection_string or not database_id:
        raise RuntimeError(
            "Lakehouse SQL analytics endpoint is not provisioned yet "
            "(sqlEndpointProperties.connectionString/id missing). Open the lakehouse once "
            "in the Fabric portal to trigger endpoint provisioning, then retry."
        )
    return connection_string, database_id, lakehouse_name


# ---------------------------------------------------------------------------
# Physical lakehouse table schemas (column name -> TMDL dataType), from
# datagen/generate_fnol_data.py. Column order here drives the emitted column order.
# ---------------------------------------------------------------------------
TABLE_SCHEMAS = {
    "Policyholder": {
        "PolicyholderId": "string", "Name": "string", "Email": "string", "Phone": "string",
        "State": "string", "TenureYears": "int64", "PriorClaimsCount": "int64",
    },
    "Vehicle": {
        "VehicleId": "string", "VIN": "string", "Make": "string", "Model": "string",
        "Year": "int64", "MarketValue": "double", "TelematicsScore": "double",
        "PriorDamageFlag": "boolean",
    },
    "Adjuster": {
        "AdjusterId": "string", "Name": "string", "Email": "string", "Specialty": "string",
        "Region": "string", "CurrentCaseload": "int64", "AvailabilityStatus": "string",
    },
    "RepairShop": {
        "ShopId": "string", "Name": "string", "Network": "string", "Region": "string",
        "AvgCycleTimeDays": "double",
    },
    "Policy": {
        "PolicyId": "string", "PolicyholderId": "string", "State": "string",
        "EffectiveDate": "string", "ExpirationDate": "string", "CoverageTypes": "string",
        "DeductibleCollision": "double", "DeductibleComprehensive": "double",
        "LiabilityLimitPerPerson": "double", "LiabilityLimitPerAccident": "double",
        "Endorsements": "string", "Status": "string",
    },
    "PolicyVehicle": {
        "PolicyId": "string", "VehicleId": "string",
    },
    "Claim": {
        "ClaimId": "string", "PolicyId": "string", "VehicleId": "string",
        "DateOfLoss": "string", "DateReported": "string", "ReportedChannel": "string",
        "LossType": "string", "LossDescription": "string", "Location": "string",
        "Severity": "string", "ReserveEstimate": "double", "AssignedShopId": "string",
        "AssignedAdjusterId": "string", "Status": "string", "FraudFlag": "boolean",
        "SubrogationEligible": "boolean",
    },
    "FraudSignal": {
        "ClaimId": "string", "SignalType": "string", "ScoreValue": "double",
    },
    "SubrogationFlag": {
        "ClaimId": "string", "AtFaultParty": "string", "ThirdPartyInsurer": "string",
        "RecoveryLikelihood": "string",
    },
}

# Junction table (backs only the many-to-many coversVehicle entity relationship; not an entity).
JUNCTION_TABLES = {"PolicyVehicle"}

# ---------------------------------------------------------------------------
# Entity definitions: name -> {table, key, columns: {colName: tmdlDataType}}
# Mirrors fabric/create_ontology.py ENTITY_SPECS exactly (same properties exposed per entity).
# ---------------------------------------------------------------------------
ENTITY_SPECS = {
    "Policyholder": {
        "table": "Policyholder", "key": "PolicyholderId",
        "columns": {
            "PolicyholderId": "string", "Name": "string", "Email": "string",
            "Phone": "string", "State": "string", "TenureYears": "int64",
            "PriorClaimsCount": "int64",
        },
    },
    "Vehicle": {
        "table": "Vehicle", "key": "VehicleId",
        "columns": {
            "VehicleId": "string", "VIN": "string", "Make": "string", "Model": "string",
            "Year": "int64", "MarketValue": "double", "TelematicsScore": "double",
            "PriorDamageFlag": "boolean",
        },
    },
    "Adjuster": {
        "table": "Adjuster", "key": "AdjusterId",
        "columns": {
            "AdjusterId": "string", "Name": "string", "Email": "string",
            "Specialty": "string", "Region": "string", "CurrentCaseload": "int64",
            "AvailabilityStatus": "string",
        },
    },
    "RepairShop": {
        "table": "RepairShop", "key": "ShopId",
        "columns": {
            "ShopId": "string", "Name": "string", "Network": "string",
            "Region": "string", "AvgCycleTimeDays": "double",
        },
    },
    "Policy": {
        "table": "Policy", "key": "PolicyId",
        "columns": {
            "PolicyId": "string", "State": "string", "EffectiveDate": "string",
            "ExpirationDate": "string", "CoverageTypes": "string",
            "DeductibleCollision": "double", "DeductibleComprehensive": "double",
            "LiabilityLimitPerPerson": "double", "LiabilityLimitPerAccident": "double",
            "Endorsements": "string", "Status": "string",
        },
    },
    "Claim": {
        "table": "Claim", "key": "ClaimId",
        "columns": {
            "ClaimId": "string", "DateOfLoss": "string", "DateReported": "string",
            "ReportedChannel": "string", "LossType": "string", "LossDescription": "string",
            "Location": "string", "Severity": "string", "ReserveEstimate": "double",
            "Status": "string", "FraudFlag": "boolean", "SubrogationEligible": "boolean",
            "AssignedAdjusterId": "string",
        },
    },
    "FraudSignal": {
        "table": "FraudSignal", "key": "ClaimId",
        "columns": {
            "ClaimId": "string", "SignalType": "string", "ScoreValue": "double",
        },
    },
    "SubrogationFlag": {
        "table": "SubrogationFlag", "key": "ClaimId",
        "columns": {
            "ClaimId": "string", "AtFaultParty": "string",
            "ThirdPartyInsurer": "string", "RecoveryLikelihood": "string",
        },
    },
}

# ---------------------------------------------------------------------------
# Descriptions and synonyms (ontology extensions: rendered as `///` doc-comments and
# `synonym` lines in the generated TMDL). Descriptions cover every entity/property/
# entityRelationship; synonyms are a small, deliberately curated set of commonly-used
# alternate names, not an exhaustive list.
# ---------------------------------------------------------------------------
ENTITY_DESCRIPTIONS = {
    "Policyholder": "A customer who holds an auto insurance policy.",
    "Vehicle": "A vehicle insured under one or more auto policies.",
    "Adjuster": "An insurance adjuster responsible for investigating and resolving claims.",
    "RepairShop": "An auto body repair shop that services damaged vehicles.",
    "Policy": "An auto insurance policy issued to a policyholder, covering one or more vehicles.",
    "Claim": "A first notice of loss (FNOL) claim filed against an auto policy.",
    "FraudSignal": "A fraud-risk signal detected for a claim.",
    "SubrogationFlag": "A subrogation opportunity flagged for a claim where a third party may be at fault.",
}

ENTITY_SYNONYMS = {
    "Policyholder": ["Customer", "Insured"],
    "Adjuster": ["Claims Adjuster"],
    "RepairShop": ["Body Shop", "Auto Body Shop"],
    "Policy": ["Auto Policy", "Insurance Policy"],
    "Claim": ["FNOL", "First Notice of Loss"],
    "FraudSignal": ["Fraud Indicator"],
    "SubrogationFlag": ["Subrogation Opportunity"],
}

PROPERTY_DESCRIPTIONS = {
    "Policyholder": {
        "PolicyholderId": "Unique identifier for the policyholder.",
        "Name": "Full name of the policyholder.",
        "Email": "Email address of the policyholder.",
        "Phone": "Phone number of the policyholder.",
        "State": "U.S. state of residence for the policyholder.",
        "TenureYears": "Number of years the policyholder has held coverage with the carrier.",
        "PriorClaimsCount": "Number of claims the policyholder has filed prior to this period.",
    },
    "Vehicle": {
        "VehicleId": "Unique identifier for the vehicle.",
        "VIN": "Vehicle identification number (VIN).",
        "Make": "Manufacturer of the vehicle.",
        "Model": "Model name of the vehicle.",
        "Year": "Model year of the vehicle.",
        "MarketValue": "Estimated current market value of the vehicle, in USD.",
        "TelematicsScore": "Telematics-based driving behavior score (0-100, higher is safer).",
        "PriorDamageFlag": "Indicates whether the vehicle has a recorded history of prior damage.",
    },
    "Adjuster": {
        "AdjusterId": "Unique identifier for the adjuster.",
        "Name": "Full name of the adjuster.",
        "Email": "Email address of the adjuster.",
        "Specialty": "Claim type specialty of the adjuster (for example Collision, Injury, Total Loss).",
        "Region": "Geographic region the adjuster covers.",
        "CurrentCaseload": "Number of claims currently assigned to the adjuster.",
        "AvailabilityStatus": "Current availability status of the adjuster (Available, Busy, or Out of Office).",
    },
    "RepairShop": {
        "ShopId": "Unique identifier for the repair shop.",
        "Name": "Name of the repair shop.",
        "Network": "Whether the shop is in-network or out-of-network with the carrier.",
        "Region": "Geographic region the repair shop serves.",
        "AvgCycleTimeDays": "Average repair cycle time, in days.",
    },
    "Policy": {
        "PolicyId": "Unique identifier for the policy.",
        "State": "U.S. state in which the policy is written.",
        "EffectiveDate": "Date the policy coverage begins.",
        "ExpirationDate": "Date the policy coverage ends.",
        "CoverageTypes": "Semicolon-separated list of coverage types included on the policy.",
        "DeductibleCollision": "Deductible amount applied to collision claims, in USD.",
        "DeductibleComprehensive": "Deductible amount applied to comprehensive claims, in USD.",
        "LiabilityLimitPerPerson": "Liability coverage limit per injured person, in USD.",
        "LiabilityLimitPerAccident": "Liability coverage limit per accident, in USD.",
        "Endorsements": "Policy endorsements/add-ons (for example Rental Reimbursement, Roadside Assistance).",
        "Status": "Current status of the policy (for example Active).",
    },
    "Claim": {
        "ClaimId": "Unique identifier for the claim (FNOL).",
        "DateOfLoss": "Date the loss event occurred.",
        "DateReported": "Date the claim was reported to the carrier.",
        "ReportedChannel": "Channel through which the claim was reported (Email, Portal, or Phone).",
        "LossType": "Type of loss (Collision, Comprehensive, Liability, or UM/UIM).",
        "LossDescription": "Free-text description of the loss.",
        "Location": "Location (city and state) where the loss occurred.",
        "Severity": "Severity tier of the claim (Minor, Moderate, Severe, or Total Loss).",
        "ReserveEstimate": "Estimated reserve amount set aside for the claim, in USD.",
        "Status": "Current status of the claim (Open, In Review, or Closed).",
        "FraudFlag": "Indicates whether the claim has been flagged for potential fraud.",
        "SubrogationEligible": "Indicates whether the claim is eligible for subrogation recovery.",
        "AssignedAdjusterId": "Identifier of the adjuster assigned to handle the claim.",
    },
    "FraudSignal": {
        "ClaimId": "Identifier of the claim this fraud signal applies to.",
        "SignalType": "Description of the fraud-risk indicator detected.",
        "ScoreValue": "Confidence score of the fraud signal (0.0-1.0).",
    },
    "SubrogationFlag": {
        "ClaimId": "Identifier of the claim this subrogation flag applies to.",
        "AtFaultParty": "Party determined to be at fault for the loss.",
        "ThirdPartyInsurer": "Name of the at-fault third party's insurance carrier.",
        "RecoveryLikelihood": "Likelihood of successful subrogation recovery (High, Medium, or Low).",
    },
}

PROPERTY_SYNONYMS = {
    "Vehicle": {"VIN": ["Vehicle Identification Number"]},
    "RepairShop": {"ShopId": ["RepairShopId"]},
    "Claim": {"ClaimId": ["FNOL Number"], "AssignedAdjusterId": ["AdjusterId"]},
}

ENTITY_RELATIONSHIP_DESCRIPTIONS = {
    "relatesToPolicy": "Links a claim to the policy it was filed under.",
    "involvesVehicle": "Links a claim to the vehicle involved in the loss.",
    "repairedAtShop": "Links a claim to the repair shop assigned to the vehicle.",
    "assignedToAdjuster": "Links a claim to the adjuster assigned to handle it.",
    "hasFraudSignal": "Links a claim to any fraud-risk signal detected for it.",
    "hasSubrogationFlag": "Links a claim to a subrogation opportunity flagged for it.",
    "belongsToPolicyholder": "Links a policy to the policyholder who holds it.",
    "coversVehicle": "Links a policy to the vehicle(s) it covers.",
}

# TOM table-to-table relationships: (name, fromTableCol, toTableCol)
RELATIONSHIPS = [
    ("rel_claim_policy", "Claim.PolicyId", "Policy.PolicyId"),
    ("rel_claim_vehicle", "Claim.VehicleId", "Vehicle.VehicleId"),
    ("rel_claim_repairshop", "Claim.AssignedShopId", "RepairShop.ShopId"),
    ("rel_claim_adjuster", "Claim.AssignedAdjusterId", "Adjuster.AdjusterId"),
    ("rel_fraudsignal_claim", "FraudSignal.ClaimId", "Claim.ClaimId"),
    ("rel_subrogationflag_claim", "SubrogationFlag.ClaimId", "Claim.ClaimId"),
    ("rel_policy_policyholder", "Policy.PolicyholderId", "Policyholder.PolicyholderId"),
    ("rel_policyvehicle_policy", "PolicyVehicle.PolicyId", "Policy.PolicyId"),
    ("rel_policyvehicle_vehicle", "PolicyVehicle.VehicleId", "Vehicle.VehicleId"),
]

# Ontology-level entity relationships: (name, fromEntity, toEntity, backingConfig)
# backingConfig is either ("relationship", tomRelName) or ("table", junctionTable, fromRel, toRel)
ENTITY_RELATIONSHIPS = [
    ("relatesToPolicy", "Claim", "Policy", ("relationship", "rel_claim_policy")),
    ("involvesVehicle", "Claim", "Vehicle", ("relationship", "rel_claim_vehicle")),
    ("repairedAtShop", "Claim", "RepairShop", ("relationship", "rel_claim_repairshop")),
    ("assignedToAdjuster", "Claim", "Adjuster", ("relationship", "rel_claim_adjuster")),
    ("hasFraudSignal", "Claim", "FraudSignal", ("relationship", "rel_fraudsignal_claim")),
    ("hasSubrogationFlag", "Claim", "SubrogationFlag", ("relationship", "rel_subrogationflag_claim")),
    ("belongsToPolicyholder", "Policy", "Policyholder", ("relationship", "rel_policy_policyholder")),
    ("coversVehicle", "Policy", "Vehicle",
     ("table", "PolicyVehicle", "rel_policyvehicle_policy", "rel_policyvehicle_vehicle")),
]


# ---------------------------------------------------------------------------
# TMDL part builders
# ---------------------------------------------------------------------------
def build_platform():
    return json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
                   "platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    })


def build_database():
    return f"database{os.linesep}{TAB}compatibilityLevel: 1000000{os.linesep}"


def build_expressions(connection_string, database_id):
    return (
        f"expression DatabaseQuery =\n"
        f"{TAB}{TAB}let\n"
        f"{TAB}{TAB}    database = Sql.Database(\"{connection_string}\", \"{database_id}\")\n"
        f"{TAB}{TAB}in\n"
        f"{TAB}{TAB}    database\n"
        f"{TAB}lineageTag: {new_tag()}\n"
    )


def build_table(name, columns, lakehouse_name=None):
    lines = [f"table {name}", f"{TAB}lineageTag: {new_tag()}", ""]
    for col, dtype in columns.items():
        lines.append(f"{TAB}column {col}")
        lines.append(f"{TAB}{TAB}dataType: {dtype}")
        lines.append(f"{TAB}{TAB}lineageTag: {new_tag()}")
        lines.append(f"{TAB}{TAB}sourceColumn: {col}")
        lines.append("")
    lines.append(f"{TAB}partition {name} = entity")
    lines.append(f"{TAB}{TAB}mode: directLake")
    lines.append(f"{TAB}{TAB}source")
    lines.append(f"{TAB}{TAB}{TAB}entityName: {name}")
    lines.append(f"{TAB}{TAB}{TAB}schemaName: dbo")
    lines.append(f"{TAB}{TAB}{TAB}expressionSource: DatabaseQuery")
    lines.append("")
    # ONT_* annotations identify which Fabric item this partition's data actually comes
    # from. Without these the portal can't resolve "the kind of Fabric item this data
    # source points to" and entity Instances show no data.
    lines.append(f"{TAB}{TAB}annotation ONT_WorkspaceId = {WORKSPACE_ID}")
    lines.append(f"{TAB}{TAB}annotation ONT_ItemId = {LAKEHOUSE_ID}")
    lines.append(f"{TAB}{TAB}annotation ONT_ItemKind = Lakehouse")
    if lakehouse_name:
        lines.append(f"{TAB}{TAB}annotation ONT_ItemName = {lakehouse_name}")
    lines.append("")
    return "\n".join(lines)


def tmdl_name(value):
    """Quotes an identifier/synonym value that contains spaces, matching TMDL's
    convention for multi-word names (e.g. entityRelationship 'Status Code For Plant')."""
    return f"'{value}'" if " " in value else value


def build_entity(name, spec):
    # TMDL field order is positional, not freeform: entity-level `synonym` must come
    # AFTER all `property` blocks (not right after `keyProperty`), and a property-level
    # `synonym` must come AFTER its `backingConfiguration` block (not before it). Getting
    # this wrong is rejected as a TMDL "Indentation" parse error even though the tabs
    # themselves are correct, per the MS Learn ontology-definition field-order tables.
    table = spec["table"]
    lines = []
    entity_desc = ENTITY_DESCRIPTIONS.get(name)
    if entity_desc:
        lines.append(f"/// {entity_desc}")
    lines.append(f"entity {name}")
    lines.append(f"{TAB}lineageTag: {new_tag()}")
    lines.append(f"{TAB}backingTable: {table}")
    lines.append(f"{TAB}keyProperty: {spec['key']}")
    lines.append("")

    prop_descriptions = PROPERTY_DESCRIPTIONS.get(name, {})
    prop_synonyms = PROPERTY_SYNONYMS.get(name, {})
    for col, dtype in spec["columns"].items():
        prop_desc = prop_descriptions.get(col)
        if prop_desc:
            lines.append(f"{TAB}/// {prop_desc}")
        lines.append(f"{TAB}property {col}")
        lines.append(f"{TAB}{TAB}dataType: {dtype}")
        lines.append(f"{TAB}{TAB}lineageTag: {new_tag()}")
        lines.append("")
        lines.append(f"{TAB}{TAB}backingConfiguration")
        lines.append(f"{TAB}{TAB}{TAB}valueColumn: {table}.{col}")
        for syn in prop_synonyms.get(col, []):
            lines.append(f"{TAB}{TAB}synonym {tmdl_name(syn)}")
        lines.append("")

    for syn in ENTITY_SYNONYMS.get(name, []):
        lines.append(f"{TAB}synonym {tmdl_name(syn)}")
    if ENTITY_SYNONYMS.get(name):
        lines.append("")
    return "\n".join(lines)


def build_relationships():
    blocks = []
    for name, from_col, to_col in RELATIONSHIPS:
        blocks.append(
            f"relationship {name}\n"
            f"{TAB}fromColumn: {from_col}\n"
            f"{TAB}toColumn: {to_col}\n"
        )
    return "\n".join(blocks)


def build_entity_relationships():
    blocks = []
    for name, from_entity, to_entity, backing in ENTITY_RELATIONSHIPS:
        rel_desc = ENTITY_RELATIONSHIP_DESCRIPTIONS.get(name)
        header = ""
        if rel_desc:
            header += f"/// {rel_desc}\n"
        header += (
            f"entityRelationship {name}\n"
            f"{TAB}lineageTag: {new_tag()}\n"
            f"{TAB}fromEntity: {from_entity}\n"
            f"{TAB}toEntity: {to_entity}\n\n"
            f"{TAB}backingConfiguration\n"
        )
        if backing[0] == "relationship":
            header += f"{TAB}{TAB}relationship: {backing[1]}\n"
        else:
            _, junction_table, from_rel, to_rel = backing
            header += (
                f"{TAB}{TAB}type: table\n"
                f"{TAB}{TAB}table: {junction_table}\n"
                f"{TAB}{TAB}fromRelationship: {from_rel}\n"
                f"{TAB}{TAB}toRelationship: {to_rel}\n"
            )
        blocks.append(header)
    return "\n".join(blocks)


def build_namespace_default():
    return f"namespace default{os.linesep}{TAB}lineageTag: default{os.linesep}"


def build_model():
    lines = ["model Model", ""]
    for table in TABLE_SCHEMAS:
        lines.append(f"ref table {table}")
    lines.append("")
    for entity in ENTITY_SPECS:
        lines.append(f"ref entity {entity}")
    lines.append("")
    lines.append("ref namespace default")
    lines.append("")
    return "\n".join(lines)


def main():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    connection_string, database_id, lakehouse_name = get_lakehouse_sql_endpoint(headers)
    print(f"Lakehouse SQL endpoint: {connection_string} (database id {database_id}, name {lakehouse_name})")

    parts = []

    def add_part(path, text):
        parts.append({"path": path, "payload": b64(text), "payloadType": "InlineBase64"})

    add_part(".platform", build_platform())
    add_part("database.tmdl", build_database())
    add_part("model.tmdl", build_model())
    add_part("namespaces/default.tmdl", build_namespace_default())
    add_part("expressions.tmdl", build_expressions(connection_string, database_id))
    add_part("relationships.tmdl", build_relationships())
    add_part("entityRelationships.tmdl", build_entity_relationships())

    for table_name, columns in TABLE_SCHEMAS.items():
        add_part(f"tables/{table_name}.tmdl", build_table(table_name, columns, lakehouse_name))

    for entity_name, spec in ENTITY_SPECS.items():
        add_part(f"entities/{entity_name}.tmdl", build_entity(entity_name, spec))

    print(f"\n=== Definition parts ({len(parts)}) ===")
    for p in parts:
        decoded = base64.b64decode(p["payload"]).decode("utf-8")
        print(f"\n--- {p['path']} ({len(decoded)} chars) ---")
        print(decoded)
    print("=== End of parts ===\n")

    body = {
        "displayName": ONTOLOGY_NAME,
        "description": "Gen 2 (TMDL) ontology over the Auto FNOL lakehouse: policyholders, "
                       "policies, vehicles, adjusters, repair shops, claims, fraud signals, "
                       "and subrogation flags. Reuses the same LH_AutoFNOL lakehouse as Gen 1.",
        "definition": {"parts": parts},
    }

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
                    with open("ontology_v2_id.txt", "w") as f:
                        try:
                            f.write(result.json().get("id", ""))
                        except Exception:
                            pass
                break
    elif resp.status_code == 201:
        data = resp.json()
        with open("ontology_v2_id.txt", "w") as f:
            f.write(data.get("id", ""))
        print("Ontology ID:", data.get("id"))


if __name__ == "__main__":
    main()
