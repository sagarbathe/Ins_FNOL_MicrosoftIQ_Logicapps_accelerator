"""
Uploads synthetic CSVs into the Fabric Lakehouse Files section (OneLake) and then
triggers the Fabric "Load Table" API to materialize each as a managed Delta table.
"""
import os
import time
import subprocess
import requests
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

session = requests.Session()
retries = Retry(total=6, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

WORKSPACE_ID = config.FABRIC_WORKSPACE_ID
LAKEHOUSE_ID = config.FABRIC_LAKEHOUSE_ID
ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"
DATA_DIR = config.DATAGEN_OUTPUT_DIR

TABLES = [
    "Policyholder", "Vehicle", "Adjuster", "RepairShop", "Policy",
    "PolicyVehicle", "Claim", "FraudSignal", "SubrogationFlag",
]

cred = AzureCliCredential()

# ---- Upload CSVs to Lakehouse Files/staging (already done - skipping) ----
SKIP_UPLOAD = False
UPLOAD_ONLY_TABLES = ["Claim"]
if not SKIP_UPLOAD:
    service_client = DataLakeServiceClient(account_url=ONELAKE_ACCOUNT_URL, credential=cred)
    fs_client = service_client.get_file_system_client(file_system=WORKSPACE_ID)

    for name in TABLES:
        if name not in UPLOAD_ONLY_TABLES:
            print(f"Skipping upload for {name} (unchanged)")
            continue
        local_path = os.path.join(DATA_DIR, f"{name}.csv")
        remote_path = f"{LAKEHOUSE_ID}/Files/staging/{name}.csv"
        file_client = fs_client.get_file_client(remote_path)
        with open(local_path, "rb") as f:
            data = f.read()
        file_client.create_file()
        file_client.append_data(data, offset=0, length=len(data))
        file_client.flush_data(len(data))
        print(f"Uploaded {name}.csv ({len(data)} bytes) -> Files/staging/{name}.csv")

# ---- Load each staged CSV into a managed Delta table ----
def get_fabric_token():
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True,
    )
    return result.stdout.strip()

token = get_fabric_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
base = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/lakehouses/{LAKEHOUSE_ID}/tables"

RESUME_FROM = "Claim"
STOP_AFTER = "Claim"
started = False
for name in TABLES:
    if name == RESUME_FROM:
        started = True
    if not started:
        print(f"Skipping {name} (already loaded)")
        continue
    body = {
        "relativePath": f"Files/staging/{name}.csv",
        "pathType": "File",
        "mode": "overwrite",
        "recursive": False,
        "formatOptions": {
            "format": "Csv",
            "header": True,
            "delimiter": ",",
        },
    }
    resp = session.post(f"{base}/{name}/load", headers=headers, json=body, timeout=60)
    print(f"Load table {name}: HTTP {resp.status_code}")
    if resp.status_code not in (200, 202):
        print(resp.text)
    elif resp.status_code == 202:
        loc = resp.headers.get("Location")
        # poll
        for _ in range(30):
            time.sleep(5)
            token = get_fabric_token()
            poll_headers = {"Authorization": f"Bearer {token}"}
            try:
                p = session.get(loc, headers=poll_headers, timeout=30)
            except Exception as e:
                print(f"  poll error, retrying: {e}")
                continue
            if p.status_code == 200:
                status = p.json().get("status")
                print(f"  {name}: {status}")
                if status in ("Completed", "Failed", "Succeeded"):
                    if status == "Failed":
                        print(p.json())
                    break
    if name == STOP_AFTER:
        print(f"Stopping after {name} as requested.")
        break
