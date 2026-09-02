import subprocess, json
import requests

tok = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "-o", "json"],
    capture_output=True, text=True, shell=True,
).stdout
token = json.loads(tok)["accessToken"]
headers = {"Authorization": f"Bearer {token}"}

# Admin API - list workspaces with Copilot-enabled capacities info
r = requests.get("https://api.fabric.microsoft.com/v1/admin/capacities", headers=headers)
print("admin capacities:", r.status_code)
print(json.dumps(r.json(), indent=2)[:3000])
