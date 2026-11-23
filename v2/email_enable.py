"""Explicitly enable only the approved mailbox watcher and exact routing rule."""

import time
from urllib.parse import quote

import requests

from v2 import runtime_digest
from v2.deploy import save
from v2.email_deploy import canonical_definition, workflow_definition
from v2.mail_source import run_graph, verify_connection, verify_protected_workflows
from v2.provision import clients


def enable_email(state):
    source = state["emailIntake"]
    if not source.get("workflowDeployed") or not source.get("backendConfigured"):
        raise ValueError("Deploy and verify the private email processor first.")
    credential, arm, _ = clients(state)
    verify_protected_workflows(state, arm)
    connection = verify_connection(state, arm)
    response = requests.get(state["azure"]["webUrl"] + "/healthz", timeout=(15, 60), allow_redirects=False)
    if (response.status_code != 200 or response.json().get("build_sha256") != runtime_digest()
            or state["application"].get("buildSha256") != runtime_digest()):
        raise ValueError("The live application does not match the verified email-capable build.")
    workflow = arm.call("GET", source["workflowId"] + "?api-version=2019-05-01")
    if (workflow.get("tags", {}).get("environmentId") != state["environmentId"]
            or canonical_definition(workflow["properties"]["definition"]) != workflow_definition()):
        raise ValueError("The owned email workflow differs from its reviewed definition.")
    values = {key: value["value"] for key, value in workflow["properties"]["parameters"].items()}
    if (values["folderId"] != source["folderId"] or values["mailbox"] != source["mailbox"]
            or not source.get("connectorFolderId")
            or values["connectorFolderId"] != source["connectorFolderId"]
            or values["subjectTag"] != source["subjectTag"]
            or values["webUrl"] != state["azure"]["webUrl"]
            or values["webAudience"] != state["teams"]["resourceUri"]
            or values["teamId"] != state["notifications"]["teamId"]
            or values["channelId"] != state["notifications"]["channelId"]
            or values["$connections"]["office365"]["connectionId"] != source["connectionId"]
            or values["$connections"]["teams"]["connectionId"] != state["notifications"]["connectionId"]):
        raise ValueError("The watcher source, private backend or notification destination changed.")
    rule_path = "/v1.0/me/mailFolders/inbox/messageRules/" + quote(source["ruleId"], safe="")
    rule = run_graph(state, credential, arm, connection, "GET", rule_path)
    if (rule["conditions"] != {"subjectContains": [source["subjectTag"]]}
            or rule["actions"] != {"moveToFolder": source["folderId"], "stopProcessingRules": True}
            or rule["displayName"] != "FNOL v2 " + state["environmentId"]):
        raise ValueError("The approved subject routing rule changed; it was not enabled.")
    arm.call("POST", source["workflowId"] + "/enable?api-version=2019-05-01")
    if arm.call("GET", source["workflowId"] + "?api-version=2019-05-01")["properties"]["state"] != "Enabled":
        raise RuntimeError("The isolated watcher enablement was not verified.")
    source["workflowEnabled"] = True
    save(state)
    if not rule["isEnabled"]:
        run_graph(state, credential, arm, connection, "PATCH", rule_path, {"isEnabled": True})
    actual = run_graph(state, credential, arm, connection, "GET", rule_path)
    if (actual.get("isEnabled") is not True or actual["conditions"] != rule["conditions"]
            or actual["actions"] != rule["actions"]):
        raise RuntimeError("Routing-rule enablement readback differs; investigate before sending a test.")
    source.update(ruleEnabled=True, enabledAt=int(time.time()), endToEndVerified=False)
    verify_protected_workflows(state, arm)
    save(state)
    print("Approved isolated-folder watcher and exact subject-routing rule enabled.")
