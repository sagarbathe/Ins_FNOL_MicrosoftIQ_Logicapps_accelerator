import subprocess, json, base64
import requests

tok = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "-o", "json"],
    capture_output=True, text=True, shell=True,
).stdout
token = json.loads(tok)["accessToken"]
headers = {"Authorization": f"Bearer {token}"}

ws = "9b291db7-d8d2-4ad7-acc6-70fcc1738bcf"
artifact = "6dc29547-619c-4224-8762-fbf1dac5904f"

r = requests.post(f"https://api.fabric.microsoft.com/v1/workspaces/{ws}/items/{artifact}/getDefinition", headers=headers)
loc = r.headers.get("Location")
print("op:", r.status_code, loc)
import time
time.sleep(4)
res = requests.get(loc + "/result", headers=headers)
data = res.json()
parts = {p["path"]: p for p in data["definition"]["parts"]}
for path in parts:
    print(path)

print()
for path in parts:
    if "datasource" in path.lower() and "published" in path.lower():
        payload = base64.b64decode(parts[path]["payload"]).decode("utf-8")
        print("===", path, "===")
        print(payload[:1500])
