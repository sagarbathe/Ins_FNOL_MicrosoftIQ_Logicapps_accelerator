"""
Adds the assignedToAdjuster edge (Claim -> Adjuster) to the AutoFNOL_Ontology's underlying
graph model (node property + edge type + edge table).

NOTE: For NEW deployments, the graph model is auto-generated from the ontology created by
create_ontology.py (which already includes the assignedToAdjuster relationship), so this
script should not be needed. Keep it only to patch a graph model that was generated before
that change and does not yet expose the assignedToAdjuster edge.
"""
import os
import subprocess, requests, json, time, base64
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

def get_token():
    out = subprocess.check_output(
        ['az', 'account', 'get-access-token', '--resource', 'https://api.fabric.microsoft.com',
         '--query', 'accessToken', '-o', 'tsv'], shell=True, text=True)
    return out.strip()

WORKSPACE_ID = config.FABRIC_WORKSPACE_ID
GRAPH_MODEL_ID = config.FABRIC_GRAPH_MODEL_ID
LAKEHOUSE_ID = config.FABRIC_LAKEHOUSE_ID
CLAIM_ALIAS = '853931770133622125'
ADJUSTER_ALIAS = '282341088111907415'

assert config.FABRIC_GRAPH_MODEL_ID, "Set FABRIC_GRAPH_MODEL_ID in .env after the ontology graph model is created in Fabric"

token = get_token()
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}

# 1. Get current definition
url = f'https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/graphModels/{GRAPH_MODEL_ID}/getDefinition'
resp = requests.post(url, headers=headers)
data = None
if resp.status_code == 202:
    loc = resp.headers.get('Location')
    for _ in range(20):
        time.sleep(3)
        poll = requests.get(loc, headers=headers)
        pdata = poll.json()
        if pdata.get('status') == 'Succeeded':
            res = requests.get(loc + '/result', headers=headers)
            data = res.json()
            break
elif resp.status_code == 200:
    data = resp.json()

parts = data['definition']['parts']
part_map = {p['path']: p for p in parts}

gt = json.loads(base64.b64decode(part_map['graphType.json']['payload']).decode('utf-8'))
gd = json.loads(base64.b64decode(part_map['graphDefinition.json']['payload']).decode('utf-8'))

# 2. Add AssignedAdjusterId property to Claim node type (if missing)
for n in gt['nodeTypes']:
    if n['alias'] == CLAIM_ALIAS:
        prop_names = [p['name'] for p in n['properties']]
        if 'AssignedAdjusterId' not in prop_names:
            n['properties'].append({"name": "AssignedAdjusterId", "type": "STRING"})
        break

# 3. Add assignedToAdjuster edge type (if missing)
edge_alias = 'assignedToAdjuster_edge'
existing_labels = [e['labels'][0] for e in gt.get('edgeTypes', [])]
if 'assignedToAdjuster' not in existing_labels:
    gt['edgeTypes'].append({
        "alias": edge_alias,
        "sourceNodeType": {"alias": CLAIM_ALIAS},
        "labels": ["assignedToAdjuster"],
        "destinationNodeType": {"alias": ADJUSTER_ALIAS},
        "properties": []
    })

# 4. Add edge table entry (if missing)
existing_edge_aliases = [e.get('edgeTypeAlias') for e in gd.get('edgeTables', [])]
if edge_alias not in existing_edge_aliases:
    gd['edgeTables'].append({
        "edgeTypeAlias": edge_alias,
        "id": "e1a2b3c4-1234-4abc-9def-000000000001",
        "edgeIdMapping": None,
        "dataSourceName": f"{LAKEHOUSE_ID}_Claim",
        "sourceNodeKeyColumns": ["ClaimId"],
        "propertyMappings": [],
        "destinationNodeKeyColumns": ["AssignedAdjusterId"]
    })

def encode(obj):
    return base64.b64encode(json.dumps(obj).encode('utf-8')).decode('utf-8')

part_map['graphType.json']['payload'] = encode(gt)
part_map['graphDefinition.json']['payload'] = encode(gd)

update_url = f'https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/graphModels/{GRAPH_MODEL_ID}/updateDefinition'
body = {"definition": {"parts": list(part_map.values())}}
resp = requests.post(update_url, headers=headers, json=body)
print('updateDefinition status:', resp.status_code)
print(resp.text[:2000])

if resp.status_code == 202:
    loc = resp.headers.get('Location')
    for _ in range(20):
        time.sleep(3)
        poll = requests.get(loc, headers=headers)
        pdata = poll.json()
        print('poll status:', pdata.get('status'))
        if pdata.get('status') in ('Succeeded', 'Failed'):
            print(pdata)
            break
