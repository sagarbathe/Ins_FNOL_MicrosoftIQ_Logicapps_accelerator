import os
import subprocess, json
import requests
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

tok = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "-o", "json"],
    capture_output=True, text=True, shell=True,
).stdout
token = json.loads(tok)["accessToken"]

ws = config.FABRIC_WORKSPACE_ID
gm = config.FABRIC_GRAPH_MODEL_ID
assert config.FABRIC_GRAPH_MODEL_ID, "Set FABRIC_GRAPH_MODEL_ID in .env after the ontology graph model is created in Fabric"
query = (
    "MATCH (p:Policy {PolicyId: 'POL-00005'}) RETURN p.CoverageTypes, "
    "p.DeductibleCollision, p.LiabilityLimitPerPerson, p.LiabilityLimitPerAccident"
)
r = requests.post(
    f"https://api.fabric.microsoft.com/v1/workspaces/{ws}/GraphModels/{gm}/executeQuery",
    headers={"Authorization": f"Bearer {token}"},
    params={"preview": "true"},
    json={"query": query},
)
print(r.status_code)
print(r.text[:3000])
