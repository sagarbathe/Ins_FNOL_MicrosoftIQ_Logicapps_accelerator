import subprocess, json
import requests

tok = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "-o", "json"],
    capture_output=True, text=True, shell=True,
).stdout
token = json.loads(tok)["accessToken"]
headers = {"Authorization": f"Bearer {token}"}

r = requests.get("https://api.fabric.microsoft.com/v1/capacities", headers=headers)
print("capacities:", r.status_code)
print(json.dumps(r.json(), indent=2))

r2 = requests.get("https://api.fabric.microsoft.com/v1/workspaces", headers=headers)
print("\nworkspaces:")
for w in r2.json().get("value", []):
    print(w.get("id"), w.get("displayName"), "capacityId=", w.get("capacityId"))
