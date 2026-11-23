"""Owned-registration administration and read-only inventory with an explicit Agent 365 gate."""

import json
import re
from datetime import datetime, timezone

import requests
from azure.ai.projects import AIProjectClient

from v2.deploy import STATE, save
from v2.mail_source import verify_protected_workflows
from v2.provision import clients

REGISTRY_PATH = "/v1.0/copilot/admin/catalog/packages"
REGISTRY_DOC = "https://learn.microsoft.com/en-us/microsoft-agent-365/admin/graph-api"


def ensure_registration_owner(state, graph, identity):
    path = "/v1.0/applications/" + identity["objectId"]
    application = graph.call("GET", path + "?$select=id,appId,tags")
    if (application["id"] != identity["objectId"] or application["appId"] != identity["clientId"]
            or state["environmentId"] not in application.get("tags", [])
            or "fnol-delegated-v2" not in application.get("tags", [])):
        raise ValueError("Refusing ownership administration for an unowned app registration.")
    owners = graph.call("GET", path + "/owners?$select=id")
    if owners.get("@odata.nextLink"):
        raise RuntimeError("The complete application-owner set was not verified.")
    if state["operatorId"] not in [item["id"] for item in owners["value"]]:
        graph.call("POST", path + "/owners/$ref", {
            "@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/" + state["operatorId"],
        })
    actual = graph.call("GET", path + "/owners?$select=id")
    if state["operatorId"] not in [item["id"] for item in actual["value"]]:
        raise RuntimeError("The native operator's app-registration ownership was not verified.")


def configure_ownership(state):
    _, arm, graph = clients(state)
    if graph.call("GET", "/v1.0/me?$select=id")["id"] != state["operatorId"]:
        raise ValueError("Only the configured native operator can establish v2 registration ownership.")
    verify_protected_workflows(state, arm)
    for identity in (state["webIdentity"], state["workiq"]["identity"]):
        ensure_registration_owner(state, graph, identity)
    inventory(state)


def registry_result(response, manifest_id):
    if response.status_code != 200:
        detail = response.json() if "application/json" in response.headers.get("Content-Type", "") else {}
        code = detail.get("error", {}).get("code", "")
        return {
            "registered": None, "verified": False, "httpStatus": response.status_code,
            "serviceCode": code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) else "",
            "requiredPermission": "CopilotPackages.Read.All",
            "adminGate": "Agent 365 entitlement and supported AI/Global administrator",
        }
    data = response.json()
    matches = [item["id"] for item in data["value"] if item.get("manifestId") == manifest_id]
    pagination_pending = bool(data.get("@odata.nextLink"))
    return {
        "registered": True if matches else (None if pagination_pending else False),
        "verified": len(matches) == 1 and not pagination_pending, "httpStatus": 200,
        "matchingPackageIds": matches, "paginationPending": pagination_pending,
        "note": (
            "A personal Teams static tab is not automatically a Copilot custom-engine agent package. "
            "No new bot identity or retired beta registry was introduced."
        ),
    }


def inventory(state):
    credential, arm, graph = clients(state)
    protected_count = verify_protected_workflows(state, arm)
    group = f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
    resources = arm.call("GET", group + "/resources?api-version=2021-04-01")
    if resources.get("nextLink"):
        raise RuntimeError("The owned resource inventory is paginated; do not claim a complete inventory.")
    owned = [{
        "id": item["id"], "name": item["name"], "type": item["type"],
        "environmentTagVerified": item.get("tags", {}).get("environmentId") == state["environmentId"],
    } for item in resources["value"]]
    registrations = []
    for app in (state["webIdentity"], state["workiq"]["identity"]):
        path = "/v1.0/applications/" + app["objectId"]
        actual = graph.call("GET", path + "?$select=id,appId,tags")
        if (actual["appId"] != app["clientId"]
                or state["environmentId"] not in actual.get("tags", [])):
            raise RuntimeError("An app-registration inventory entry is not owned by this environment.")
        owners = graph.call("GET", path + "/owners?$select=id")
        if owners.get("@odata.nextLink"):
            raise RuntimeError("The native v2 application-owner inventory is incomplete.")
        registrations.append({
            "objectId": app["objectId"], "clientId": app["clientId"],
            "nativeOperatorOwnerVerified": state["operatorId"] in [item["id"] for item in owners["value"]],
        })
    operator_owner = all(item["nativeOperatorOwnerVerified"] for item in registrations)
    with AIProjectClient(endpoint=state["azure"]["projectEndpoint"], credential=credential) as project:
        agent = project.agents.get_version(
            agent_name=state["agent"]["name"], agent_version=state["agent"]["version"],
        )
        tools = agent.definition.as_dict()["tools"]
    connections = []
    for tool in tools:
        path = tool["project_connection_id"]
        if not path.startswith(state["azure"]["projectId"] + "/connections/"):
            raise RuntimeError("A pinned agent tool references a connection outside the owned project.")
        resource = arm.call("GET", path + "?api-version=2025-10-01-preview")
        connections.append({
            "id": path, "serverLabel": tool["server_label"],
            "authType": resource["properties"]["authType"],
            "allowedTools": tool.get("allowed_tools"),
            "approvalPolicy": tool.get("require_approval"),
        })
    response = requests.get(
        "https://graph.microsoft.com" + REGISTRY_PATH,
        headers={"Authorization": "Bearer " + credential.get_token(
            "https://graph.microsoft.com/.default",
        ).token}, timeout=(15, 60), allow_redirects=False,
    )
    registry = registry_result(response, state["teams"]["manifestId"])
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(), "environmentId": state["environmentId"],
        "protectedWorkflows": {"configuredCount": protected_count, "verifiedCount": protected_count},
        "resources": owned,
        "untaggedResourcesRequireReview": [item["id"] for item in owned if not item["environmentTagVerified"]],
        "nativeOperatorOwnerVerified": operator_owner, "appRegistrations": registrations,
        "pinnedAgent": state["agent"],
        "connections": connections, "agent365": {**registry, "documentation": REGISTRY_DOC},
        "sourcePermissionsChanged": False, "newPublishedBotCreated": False,
    }
    target = STATE.parent / "governance-inventory.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    state["governance"] = {
        "inventoryVerified": True, "inventoryPath": target.name,
        "registryVerified": registry["verified"], "registryHttpStatus": response.status_code,
        "nativeOperatorOwnerVerified": operator_owner,
        "untaggedResourceCount": len(report["untaggedResourcesRequireReview"]),
        "endToEndVerified": (operator_owner and registry["verified"]
                            and not report["untaggedResourcesRequireReview"]),
    }
    save(state)
    print("V2 resource/tool inventory recorded. Native operator application ownership:",
          "verified." if operator_owner else "missing; no directory ownership grant was changed.")
    print("Agent 365 registry verification:",
          "verified" if registry["verified"] else f"not verified (HTTP {response.status_code}).")
