"""
Configures the Fabric Data Agent (DA_AutoFNOL_Ontology) with a Lakehouse tables
datasource covering all 9 Auto FNOL tables, AI instructions, and few-shot examples,
then publishes it.
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
LAKEHOUSE_ID = config.FABRIC_LAKEHOUSE_ID
DATA_AGENT_ID = config.FABRIC_DATA_AGENT_ID

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


def col(name, dtype, desc):
    return {"display_name": name, "type": "lakehouse_tables.column", "data_type": dtype, "description": desc}


def table(name, desc, columns):
    return {
        "display_name": name,
        "type": "lakehouse_tables.table",
        "is_selected": True,
        "description": desc,
        "children": columns,
    }


tables = [
    table("Policyholder", "Policyholder master data", [
        col("PolicyholderId", "string", "Unique policyholder identifier"),
        col("Name", "string", "Policyholder full name"),
        col("Email", "string", "Policyholder email"),
        col("Phone", "string", "Policyholder phone number"),
        col("State", "string", "US state of residence"),
        col("TenureYears", "int", "Years as a customer"),
        col("PriorClaimsCount", "int", "Number of prior claims filed"),
    ]),
    table("Vehicle", "Insured vehicle data", [
        col("VehicleId", "string", "Unique vehicle identifier"),
        col("VIN", "string", "Vehicle identification number"),
        col("Make", "string", "Vehicle make"),
        col("Model", "string", "Vehicle model"),
        col("Year", "int", "Model year"),
        col("MarketValue", "double", "Current market value in USD"),
        col("TelematicsScore", "double", "Driving behavior score from telematics (0-100)"),
        col("PriorDamageFlag", "boolean", "Whether the vehicle has prior recorded damage"),
    ]),
    table("Adjuster", "Claims adjuster roster", [
        col("AdjusterId", "string", "Unique adjuster identifier"),
        col("Name", "string", "Adjuster full name"),
        col("Email", "string", "Adjuster email"),
        col("Specialty", "string", "Adjuster specialty: Collision, Comprehensive, Total Loss, Injury, General"),
        col("Region", "string", "Adjuster's assigned region"),
        col("CurrentCaseload", "int", "Number of claims currently assigned"),
        col("AvailabilityStatus", "string", "Available, Busy, or Out of Office"),
    ]),
    table("RepairShop", "Repair shop network data", [
        col("ShopId", "string", "Unique repair shop identifier"),
        col("Name", "string", "Repair shop name"),
        col("Network", "string", "In-Network or Out-of-Network"),
        col("Region", "string", "Region served"),
        col("AvgCycleTimeDays", "double", "Average repair cycle time in days"),
    ]),
    table("Policy", "Auto insurance policy data", [
        col("PolicyId", "string", "Unique policy identifier"),
        col("PolicyholderId", "string", "Foreign key to Policyholder"),
        col("State", "string", "State the policy is issued in"),
        col("EffectiveDate", "date", "Policy effective date"),
        col("ExpirationDate", "date", "Policy expiration date"),
        col("CoverageTypes", "string", "Semicolon-separated coverage types: Liability, Collision, Comprehensive, UM/UIM"),
        col("DeductibleCollision", "double", "Collision coverage deductible in USD"),
        col("DeductibleComprehensive", "double", "Comprehensive coverage deductible in USD"),
        col("LiabilityLimitPerPerson", "double", "Liability limit per person in USD"),
        col("LiabilityLimitPerAccident", "double", "Liability limit per accident in USD"),
        col("Endorsements", "string", "Policy endorsements, if any"),
        col("Status", "string", "Policy status, e.g. Active"),
    ]),
    table("PolicyVehicle", "Mapping of policies to insured vehicles", [
        col("PolicyId", "string", "Foreign key to Policy"),
        col("VehicleId", "string", "Foreign key to Vehicle"),
    ]),
    table("Claim", "Auto claims data including FNOL details", [
        col("ClaimId", "string", "Unique claim identifier"),
        col("PolicyId", "string", "Foreign key to Policy"),
        col("VehicleId", "string", "Foreign key to Vehicle"),
        col("DateOfLoss", "date", "Date the loss occurred"),
        col("DateReported", "date", "Date the FNOL was reported"),
        col("ReportedChannel", "string", "Email, Portal, or Phone"),
        col("LossType", "string", "Collision, Comprehensive, Liability, or UM/UIM"),
        col("LossDescription", "string", "Free-text description of the loss"),
        col("Location", "string", "City/state where the loss occurred"),
        col("Severity", "string", "Minor, Moderate, Severe, or Total Loss"),
        col("ReserveEstimate", "double", "Estimated reserve amount in USD"),
        col("AssignedShopId", "string", "Foreign key to RepairShop"),
        col("Status", "string", "Open, In Review, or Closed"),
        col("FraudFlag", "boolean", "Whether the claim is flagged for potential fraud"),
        col("SubrogationEligible", "boolean", "Whether the claim is eligible for subrogation recovery"),
    ]),
    table("FraudSignal", "Fraud signal detail for flagged claims", [
        col("ClaimId", "string", "Foreign key to Claim"),
        col("SignalType", "string", "Description of the fraud signal detected"),
        col("ScoreValue", "double", "Fraud signal confidence score (0-1)"),
    ]),
    table("SubrogationFlag", "Subrogation opportunity detail", [
        col("ClaimId", "string", "Foreign key to Claim"),
        col("AtFaultParty", "string", "Party determined to be at fault"),
        col("ThirdPartyInsurer", "string", "Name of the third-party insurer"),
        col("RecoveryLikelihood", "string", "High, Medium, or Low likelihood of recovery"),
    ]),
]

datasource = {
    "$schema": "1.0.0",
    "artifactId": LAKEHOUSE_ID,
    "workspaceId": WORKSPACE_ID,
    "displayName": "LH_AutoFNOL",
    "type": "lakehouse_tables",
    "userDescription": "Auto FNOL Triage lakehouse: policyholders, policies, vehicles, adjusters, "
                        "repair shops, claims, fraud signals, and subrogation flags.",
    "dataSourceInstructions": (
        "Use this data source to answer questions about policy coverage and status, vehicle "
        "details, claim history, adjuster availability/caseload, repair shop network status, "
        "fraud signals, and subrogation opportunities. Join Claim to Policy via PolicyId, Policy "
        "to Policyholder via PolicyholderId, Claim to Vehicle via VehicleId, and Claim to "
        "RepairShop via AssignedShopId. FraudSignal and SubrogationFlag join to Claim via ClaimId."
    ),
    "elements": [
        {
            "display_name": "dbo",
            "type": "lakehouse_tables.schema",
            "is_selected": True,
            "children": tables,
        }
    ],
}

data_agent_json = {"$schema": "2.1.0"}

stage_config = {
    "$schema": "1.0.0",
    "aiInstructions": (
        "You are the Fabric Ontology Data Agent for an Auto FNOL (First Notice of Loss) Triage "
        "solution. Answer natural-language questions about policyholders, policies, vehicles, "
        "adjusters, repair shops, claims, fraud signals, and subrogation opportunities using the "
        "connected Lakehouse tables. Always ground answers in the actual data - do not speculate. "
        "When asked about coverage, check the Policy table's CoverageTypes, deductibles, and "
        "liability limits. When asked about adjuster routing, consider CurrentCaseload, "
        "AvailabilityStatus, Specialty, and Region together. When asked about fraud or "
        "subrogation, join Claim to FraudSignal or SubrogationFlag respectively. Be concise and "
        "return structured, tabular answers where appropriate."
    ),
}

fewshots = {
    "$schema": "1.0.0",
    "fewShots": [
        {
            "id": str(uuid.uuid4()),
            "question": "What is the coverage and deductible on policy POL-00001?",
            "query": "SELECT PolicyId, CoverageTypes, DeductibleCollision, DeductibleComprehensive, "
                     "LiabilityLimitPerPerson, LiabilityLimitPerAccident FROM Policy WHERE PolicyId = 'POL-00001'",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Which adjusters are available and have the lowest caseload in the Midwest region?",
            "query": "SELECT AdjusterId, Name, Specialty, CurrentCaseload FROM Adjuster "
                     "WHERE Region = 'Midwest' AND AvailabilityStatus = 'Available' "
                     "ORDER BY CurrentCaseload ASC",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "Show all claims flagged for fraud along with their signal type and score.",
            "query": "SELECT c.ClaimId, c.LossDescription, f.SignalType, f.ScoreValue FROM Claim c "
                     "JOIN FraudSignal f ON c.ClaimId = f.ClaimId WHERE c.FraudFlag = true",
        },
        {
            "id": str(uuid.uuid4()),
            "question": "How many prior claims has the policyholder on policy POL-00001 filed?",
            "query": "SELECT ph.PriorClaimsCount FROM Policyholder ph "
                     "JOIN Policy p ON ph.PolicyholderId = p.PolicyholderId WHERE p.PolicyId = 'POL-00001'",
        },
    ],
}

parts = [
    {"path": "Files/Config/data_agent.json", "payload": b64(data_agent_json), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/stage_config.json", "payload": b64(stage_config), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/lakehouse-LH_AutoFNOL/datasource.json", "payload": b64(datasource), "payloadType": "InlineBase64"},
    {"path": "Files/Config/draft/lakehouse-LH_AutoFNOL/fewshots.json", "payload": b64(fewshots), "payloadType": "InlineBase64"},
]

body = {"definition": {"parts": parts}}

token = get_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items/{DATA_AGENT_ID}/updateDefinition"
resp = requests.post(url, headers=headers, json=body)
print("updateDefinition:", resp.status_code)
print(resp.text[:2000])
