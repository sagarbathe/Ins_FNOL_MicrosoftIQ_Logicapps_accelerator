"""Owned, operator-authenticated setup of the approved v2 mailbox source."""

import hashlib
import json
import re
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from v2.deploy import save
from v2.provision import clients


def verify_protected_workflows(state, arm):
    protected = state.get("protectedWorkflows", [])
    legacy = state.get("emailIntake", {}).get("protectedV1Workflows", [])
    if "protectedWorkflows" not in state and not legacy:
        raise ValueError("Configure protectedWorkflows explicitly; use an empty list for a standalone deployment.")
    if not isinstance(protected, list) or not isinstance(legacy, list):
        raise ValueError("Protected workflow fingerprints must be lists.")
    if "protectedV1Workflows" in state.get("emailIntake", {}) and not legacy:
        raise ValueError("An existing legacy protection list must not be emptied.")
    # Existing pilot fingerprints remain mandatory even when the new list is empty.
    protected = protected + legacy
    for item in protected:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                or not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Each protected workflow requires its resource ID and actual SHA-256 fingerprint.")
    for item in protected:
        properties = arm.call("GET", item["id"] + "?api-version=2019-05-01")["properties"]
        snapshot = {key: properties.get(key) for key in ("definition", "parameters", "state")}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("A protected workflow changed; deployment stopped.")
    return len(protected)


def verify_connection(state, arm):
    source = state["emailIntake"]
    expected = (
        f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
        f"/providers/Microsoft.Web/connections/{state['prefix']}-email-intake"
    )
    if source.get("connectionId") != expected:
        raise ValueError("Only the separately owned v2 mail connection may be used.")
    connection = arm.call("GET", expected + "?api-version=2016-06-01")
    properties = connection["properties"]
    if (connection.get("tags", {}).get("environmentId") != state["environmentId"]
            or not properties["api"]["id"].endswith("/managedApis/office365")):
        raise ValueError("Mail connection ownership/provider readback differs.")
    actual = properties.get("authenticatedUser", {}).get("name", "")
    if actual.casefold() != source["mailbox"].casefold():
        raise ValueError("The mail connection is not authorized as the approved intake mailbox.")
    if not any(item.get("status") == "Connected" for item in properties.get("statuses", [])):
        raise ValueError("Authorize the separate v2 Outlook connection before email setup.")
    return connection


def request_access(state):
    return {"triggers": {
        "sasAuthenticationPolicy": {"state": "Disabled"},
        "openAuthenticationPolicies": {"policies": {"operator": {
            "type": "AAD", "claims": [
                {"name": "iss", "value": "https://sts.windows.net/" + state["tenantId"] + "/"},
                {"name": "aud", "value": "https://management.azure.com"},
                {"name": "oid", "value": state["operatorId"]},
            ],
        }}},
    }}


def graph_definition(method, path, body=None):
    if (method not in ("GET", "POST", "PATCH")
            or not re.match(r"^/v1\.0/me/mailFolders(?:[/?]|$)", path)
            or any(character in path for character in ("\r", "\n", "#"))):
        raise ValueError("Mailbox setup is restricted to explicit mail-folder operations.")
    inputs = {
        "host": {"connection": {"name": "@parameters('$connections')['office365']['connectionId']"}},
        "method": "post", "path": "/codeless/httprequest",
        "headers": {"Uri": "https://graph.microsoft.com" + path, "Method": method,
                    "ContentType": "application/json"},
        "retryPolicy": {"type": "none"},
    }
    if body is not None:
        inputs["body"] = json.dumps(body)
    secure = {"secureData": {"properties": ["inputs", "outputs"]}}
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"$connections": {"type": "Object"}},
        "triggers": {"Configure": {
            "type": "Request", "kind": "Http", "operationOptions": "EnableSchemaValidation",
            "inputs": {"schema": {"type": "object", "additionalProperties": False}},
        }},
        "actions": {
            "Mailbox_operation": {"type": "ApiConnection", "inputs": inputs,
                                  "runtimeConfiguration": secure, "runAfter": {}},
            "Configured": {
                "type": "Response", "runAfter": {"Mailbox_operation": ["Succeeded"]},
                "runtimeConfiguration": {"secureData": {"properties": ["inputs"]}},
                "inputs": {"statusCode": 200, "body": {"result": "@body('Mailbox_operation')"}},
            },
            "Rejected": {
                "type": "Response", "runAfter": {"Mailbox_operation": ["Failed", "TimedOut"]},
                "inputs": {"statusCode": 502, "body": {
                    "status": "@outputs('Mailbox_operation')?['statusCode']",
                    "code": "@body('Mailbox_operation')?['error']?['code']",
                }},
            },
        },
    }


def run_graph(state, credential, arm, connection, method, path, body=None):
    result = run_mail_operation(
        state, credential, arm, connection, graph_definition(method, path, body), method, path,
    )
    if not isinstance(result, dict) or set(result) != {"result"}:
        raise RuntimeError("The fixed mailbox operation returned an invalid response envelope.")
    return result["result"]


def connector_folder_id(state, credential, arm, connection):
    source = state["emailIntake"]
    matches = []
    for skip in range(0, 1000, 100):
        definition = graph_definition("GET", "/v1.0/me/mailFolders")
        definition["actions"]["Mailbox_operation"]["inputs"] = {
            "host": {"connection": {"name": "@parameters('$connections')['office365']['connectionId']"}},
            "method": "get", "path": "/OnFilePickerOpen", "retryPolicy": {"type": "none"},
            "queries": {"operation": "MailFolders", "top": 100, "skip": skip},
        }
        result = run_mail_operation(
            state, credential, arm, connection, definition, "GET",
            "/OnFilePickerOpen?operation=MailFolders&skip=" + str(skip),
        )["result"]
        rows = result["value"]
        matches.extend(item["Id"] for item in rows
                       if item["DisplayName"] == source["folderName"] and item["IsFolder"] is True)
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("The connector root-folder inventory exceeded its explicit inspection limit.")
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0]:
        raise RuntimeError("The approved root folder did not resolve uniquely in the connector picker.")
    return matches[0]


def run_mail_operation(state, credential, arm, connection, definition, method, path):
    source = state["emailIntake"]
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    previous = source.get("setupOperation", {})
    if previous.get("status") == "submitted":
        raise RuntimeError(
            "A mailbox setup operation has an unconfirmed outcome. Inspect its protected run "
            "before retrying; no second request was submitted."
        )
    workflow_id = (
        f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
        f"/providers/Microsoft.Logic/workflows/{state['prefix']}-mail-config"
    )
    resource_path = workflow_id + "?api-version=2019-05-01"
    existing = arm.call("GET", resource_path, missing_ok=True)
    if existing and existing.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The mail configuration workflow is not owned by v2.")
    parameters = {"$connections": {"value": {"office365": {
        "connectionId": source["connectionId"],
        "connectionName": source["connectionId"].rsplit("/", 1)[1],
        "id": connection["properties"]["api"]["id"],
    }}}}
    access = request_access(state)
    arm.call("PUT", resource_path, {
        "location": state["location"],
        "tags": {"environmentId": state["environmentId"], "solution": "fnol-delegated-v2"},
        "properties": {"state": "Disabled", "definition": definition,
                       "parameters": parameters, "accessControl": access},
    })
    actual = arm.call("GET", resource_path)["properties"]
    if (actual["definition"] != definition or actual["parameters"] != parameters
            or actual["accessControl"]["triggers"] != access["triggers"]):
        raise RuntimeError("The operator-only mailbox setup workflow readback differs.")
    source["setupWorkflowId"] = workflow_id
    tracking = str(uuid.uuid4())
    source["setupOperation"] = {
        "definitionSha256": digest, "status": "prepared", "trackingId": tracking,
        "method": method, "path": path,
    }
    save(state)
    arm.call("POST", workflow_id + "/enable?api-version=2019-05-01")
    try:
        callback = arm.call(
            "POST", workflow_id + "/triggers/Configure/listCallbackUrl?api-version=2016-06-01",
        )["value"]
        parsed = urlparse(callback)
        if parsed.scheme != "https" or not parsed.hostname.endswith(".logic.azure.com"):
            raise ValueError("Azure returned an untrusted workflow callback host.")
        query = [(key, value) for key, value in parse_qsl(parsed.query)
                 if key.casefold() not in ("sig", "sp", "sv")]
        callback = urlunparse(parsed._replace(query=urlencode(query)))
        source["setupOperation"]["status"] = "submitted"
        save(state)
        response = requests.post(
            callback, json={}, headers={
                "Authorization": "Bearer " + credential.get_token(
                    "https://management.azure.com/.default",
                ).token, "x-ms-client-tracking-id": tracking,
            }, timeout=(15, 120), allow_redirects=False,
        )
        if response.status_code != 200:
            source["setupOperation"]["status"] = "rejected"
            source["setupOperation"]["httpStatus"] = response.status_code
            detail = response.json() if "application/json" in response.headers.get("Content-Type", "") else {}
            code = detail.get("code", "")
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else ""
            source["setupOperation"]["serviceCode"] = code
            save(state)
            raise RuntimeError(
                f"Mailbox setup failed: HTTP {response.status_code}, service code {code or 'unavailable'}. "
                "No email watcher was enabled."
            )
        result = response.json()
        source["setupOperation"]["status"] = "verified"
        save(state)
        return result
    finally:
        arm.call("POST", workflow_id + "/disable?api-version=2019-05-01")
        verify_protected_workflows(state, arm)


def configure_source(state):
    source = state.get("emailIntake", {})
    if not source.get("sourceIsolationApproved"):
        raise ValueError("Explicit mailbox, folder and subject-routing approval is required.")
    if (not source.get("folderName") or source["folderName"].casefold() == "inbox"
            or not re.fullmatch(r"\[[A-Z0-9-]{3,30}\]", source.get("subjectTag", ""))):
        raise ValueError("Use the approved non-Inbox folder and bounded v2 subject tag.")
    if source.get("workflowEnabled"):
        raise ValueError("Stop and review a running email intake before changing its source.")
    credential, arm, _ = clients(state)
    verify_protected_workflows(state, arm)
    connection = verify_connection(state, arm)

    def graph(method, path, body=None):
        return run_graph(state, credential, arm, connection, method, path, body)

    # Check rule permission before creating a folder or changing mailbox routing.
    rules = graph("GET", "/v1.0/me/mailFolders/inbox/messageRules")
    folders = graph("GET", "/v1.0/me/mailFolders?$top=100&$select=id,displayName,totalItemCount")
    if folders.get("@odata.nextLink") or rules.get("@odata.nextLink"):
        raise RuntimeError("Mailbox configuration is paginated; inspect the source before adoption.")
    matches = [item for item in folders["value"] if item["displayName"] == source["folderName"]]
    if len(matches) > 1:
        raise ValueError("The approved v2 folder is ambiguous.")
    if matches:
        folder = matches[0]
        if (source.get("folderId") not in (None, folder["id"])
                or (not source.get("folderId") and folder.get("totalItemCount") != 0)):
            raise ValueError("Refusing to adopt a different or nonempty untracked mailbox folder.")
    else:
        folder = graph("POST", "/v1.0/me/mailFolders", {"displayName": source["folderName"]})
    source["folderId"] = folder["id"]
    save(state)
    name = "FNOL v2 " + state["environmentId"]
    conditions = {"subjectContains": [source["subjectTag"]]}
    actions = {"moveToFolder": folder["id"], "stopProcessingRules": True}
    matches = [item for item in rules["value"] if item["displayName"] == name]
    if len(matches) > 1:
        raise ValueError("The v2 routing rule is ambiguous.")
    if matches:
        rule = matches[0]
    else:
        rule = graph("POST", "/v1.0/me/mailFolders/inbox/messageRules", {
            "displayName": name, "sequence": 1, "isEnabled": False,
            "conditions": conditions, "actions": actions,
        })
    if (rule.get("isEnabled") is not False or rule.get("conditions") != conditions
            or rule.get("actions") != actions):
        raise ValueError("Existing routing rule differs; it was not overwritten or enabled.")
    source.update(ruleId=rule["id"], ruleEnabled=False, sourceConfigured=True)
    verify_protected_workflows(state, arm)
    save(state)
    print("Approved mailbox folder and disabled v2 routing rule verified; email intake is not enabled.")
