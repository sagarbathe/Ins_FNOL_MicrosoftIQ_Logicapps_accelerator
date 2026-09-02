"""
Reconfigures the Fabric Data Agent (DA_AutoFNOL_Ontology) to query the AutoFNOL_Ontology's
auto-generated Graph Model instead of the raw LH_AutoFNOL lakehouse tables directly.

Layer stack (matches microsoft-iq-solution-accelerator pattern):
  Lakehouse (LH_AutoFNOL) -> Ontology (AutoFNOL_Ontology) -> Graph Model (auto-generated)
  -> Data Agent (this script) -> Copilot Studio / Foundry agents consume the Data Agent.
"""
import base64
import json
import os
import subprocess
import sys
import uuid
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

WORKSPACE_ID = config.FABRIC_WORKSPACE_ID
ONTOLOGY_ID = config.FABRIC_ONTOLOGY_ID
GRAPH_MODEL_ID = config.FABRIC_GRAPH_MODEL_ID
DATA_AGENT_ID = config.FABRIC_DATA_AGENT_ID

assert config.FABRIC_ONTOLOGY_ID, "Set FABRIC_ONTOLOGY_ID in .env after running fabric/create_ontology.py and copying fabric/ontology_id.txt"
assert config.FABRIC_GRAPH_MODEL_ID, "Set FABRIC_GRAPH_MODEL_ID in .env after the ontology graph model is created in Fabric"
assert config.FABRIC_DATA_AGENT_ID, "Set FABRIC_DATA_AGENT_ID in .env after creating the Data Agent item in the Fabric portal"


def get_token():
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True,
    )
    return result.stdout.strip()


def b64(obj):
    return base64.b64encode(json.dumps(obj, indent=2).encode("utf-8")).decode("utf-8")


# Node types (entities) exposed by the ontology's graph model
NODE_TYPES = [
    ("Policyholder", "Individual or business that holds an auto insurance policy."),
    ("Vehicle", "Insured vehicle covered under a policy."),
    ("Adjuster", "Claims adjuster who investigates and settles claims."),
    ("RepairShop", "Network repair shop that services damaged vehicles."),
    ("Policy", "Auto insurance policy with coverage, deductibles, and liability limits."),
    ("Claim", "Auto claim including FNOL (First Notice of Loss) details."),
    ("FraudSignal", "Fraud detection signal associated with a claim."),
    ("SubrogationFlag", "Subrogation recovery opportunity associated with a claim."),
]

# Edge types (relationships) exposed by the ontology's graph model
EDGE_TYPES = [
    ("relatesToPolicy", "Connects a Claim to the Policy it was filed under."),
    ("involvesVehicle", "Connects a Claim to the Vehicle involved in the loss."),
    ("repairedAtShop", "Connects a Claim to the RepairShop assigned to repair the vehicle."),
    ("hasFraudSignal", "Connects a Claim to any FraudSignal detected against it."),
    ("hasSubrogationFlag", "Connects a Claim to its SubrogationFlag, if eligible."),
    ("belongsToPolicyholder", "Connects a Policy to the Policyholder who owns it."),
    ("coversVehicle", "Connects a Policy to the Vehicle(s) it covers."),
    ("assignedToAdjuster", "Connects a Claim to the Adjuster assigned to investigate/settle it."),
]

node_elements = [
    {
        "id": str(uuid.uuid4()),
        "display_name": name,
        "type": "graph.nodeType",
        "is_selected": True,
        "description": desc,
    }
    for name, desc in NODE_TYPES
]

edge_elements = [
    {
        "id": str(uuid.uuid4()),
        "display_name": name,
        "type": "graph.edgeType",
        "is_selected": True,
        "description": desc,
    }
    for name, desc in EDGE_TYPES
]

datasource = {
    "$schema": "1.0.0",
    "artifactId": GRAPH_MODEL_ID,
    "workspaceId": WORKSPACE_ID,
    "displayName": "AutoFNOL_Ontology",
    "type": "graph",
    "userDescription": (
        "Auto FNOL Ontology graph: governed business entities (Policyholder, Vehicle, Adjuster, "
        "RepairShop, Policy, Claim, FraudSignal, SubrogationFlag) and their relationships, built "
        "on top of the LH_AutoFNOL lakehouse tables via the AutoFNOL_Ontology Fabric IQ item."
    ),
    "dataSourceInstructions": (
        "Use this ontology graph to answer questions about policy coverage and status, vehicle "
        "details, claim history, adjuster availability/caseload, repair shop network status, "
        "fraud signals, and subrogation opportunities. Traverse relationships rather than raw "
        "table joins: Claim-relatesToPolicy->Policy, Claim-involvesVehicle->Vehicle, "
        "Claim-repairedAtShop->RepairShop, Claim-hasFraudSignal->FraudSignal, "
        "Claim-hasSubrogationFlag->SubrogationFlag, Claim-assignedToAdjuster->Adjuster, "
        "Policy-belongsToPolicyholder->Policyholder, "
        "Policy-coversVehicle->Vehicle. Ground every answer in the governed ontology entities and "
        "relationships - do not speculate beyond what the graph returns."
    ),
    "elements": node_elements + edge_elements,
}

data_agent_json = {"$schema": "2.1.0"}

stage_config = {
    "$schema": "1.0.0",
    "aiInstructions": (
        "You are the Fabric Ontology Data Agent for an Auto FNOL (First Notice of Loss) Triage "
        "solution. Answer natural-language questions using the AutoFNOL_Ontology graph, which "
        "models Policyholder, Vehicle, Adjuster, RepairShop, Policy, Claim, FraudSignal, and "
        "SubrogationFlag entities and the governed relationships between them (relatesToPolicy, "
        "involvesVehicle, repairedAtShop, hasFraudSignal, hasSubrogationFlag, "
        "belongsToPolicyholder, coversVehicle). Always ground answers in the ontology graph - do "
        "not speculate. When asked about coverage, check the Policy entity's CoverageTypes, "
        "deductibles, and liability limits. When asked which adjuster is assigned to a claim, "
        "traverse Claim-assignedToAdjuster->Adjuster to get the adjuster's Name, Specialty, "
        "Region, CurrentCaseload, and AvailabilityStatus. When asked about adjuster routing for "
        "an unassigned claim, consider CurrentCaseload, AvailabilityStatus, Specialty, and Region "
        "together. When asked about fraud or subrogation, traverse the "
        "Claim's hasFraudSignal or hasSubrogationFlag relationship. Be concise and return "
        "structured, tabular answers where appropriate."
    ),
}

fewshots = {
    "$schema": "1.0.0",
    "fewShots": [
        {
            "id": str(uuid.uuid4()),
            "question": "What is the coverage and deductible on policy POL-00001?",
            "query": "MATCH (p:Policy {PolicyId: 'POL-00001'}) RETURN p.CoverageTypes, "
                     "p.DeductibleCollision, p.DeductibleComprehensive, p.LiabilityLimitPerPerson, "
                     "p.LiabilityLimitPerAccident",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Which adjusters are available and have the lowest caseload in the Midwest region?",
            "query": "MATCH (a:Adjuster) WHERE a.Region = 'Midwest' AND a.AvailabilityStatus = "
                     "'Available' RETURN a.AdjusterId, a.Name, a.Specialty, a.CurrentCaseload "
                     "ORDER BY a.CurrentCaseload ASC",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Show all claims flagged for fraud along with their signal type and score.",
            "query": "MATCH (c:Claim)-[:hasFraudSignal]->(f:FraudSignal) WHERE c.FraudFlag = true "
                     "RETURN c.ClaimId, c.LossDescription, f.SignalType, f.ScoreValue",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "How many prior claims has the policyholder on policy POL-00001 filed?",
            "query": "MATCH (p:Policy {PolicyId: 'POL-00001'})-[:belongsToPolicyholder]->"
                     "(ph:Policyholder) RETURN ph.PriorClaimsCount",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Which repair shop is assigned to claim CLM-00007 and what is its average cycle time?",
            "query": "MATCH (c:Claim {ClaimId: 'CLM-00007'})-[:repairedAtShop]->(r:RepairShop) "
                     "RETURN r.Name, r.Network, r.AvgCycleTimeDays",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Which adjuster is assigned to claim CLM-00007?",
            "query": "MATCH (c:Claim {ClaimId: 'CLM-00007'})-[:assignedToAdjuster]->(a:Adjuster) "
                     "RETURN a.AdjusterId, a.Name, a.Specialty, a.Region, a.CurrentCaseload, "
                     "a.AvailabilityStatus",
        },
    ],
}

parts = [
    {"path": "Files/Config/data_agent.json", "payload": b64(data_agent_json), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/stage_config.json", "payload": b64(stage_config), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/graph-AutoFNOL_Ontology/datasource.json", "payload": b64(datasource), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/graph-AutoFNOL_Ontology/fewshots.json", "payload": b64(fewshots), "payloadType": "InlineBase64"},
]

body = {"definition": {"parts": parts}}

token = get_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items/{DATA_AGENT_ID}/updateDefinition"
resp = requests.post(url, headers=headers, json=body)
print("updateDefinition:", resp.status_code)
print(resp.text[:2000])

if resp.status_code == 202:
    import time
    loc = resp.headers.get("Location")
    for _ in range(20):
        time.sleep(3)
        poll = requests.get(loc, headers=headers)
        data = poll.json()
        print("status:", data.get("status"))
        if data.get("status") in ("Succeeded", "Failed"):
            print(json.dumps(data, indent=2)[:2000])
            break

# Publish the updated draft to production stage
publish_url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/dataAgents/{DATA_AGENT_ID}/staging/publish"
pub_resp = requests.post(publish_url, headers=headers, json={
    "publishedDescription": "Auto FNOL Ontology Data Agent - queries the AutoFNOL_Ontology graph "
                             "(policyholders, vehicles, adjusters, repair shops, policies, claims, "
                             "fraud signals, subrogation flags) instead of raw lakehouse tables."
})
print("publish:", pub_resp.status_code, pub_resp.text[:1000])
