"""Provision the private Teams notification destination without synthetic intake."""

import re
import time
import uuid

import requests

from v2.deploy import save
from v2.provision import clients


def ensure_pilot_team(state, credential, graph):
    target = state.setdefault("notifications", {})
    marker = "fnol-delegated-v2:" + state["environmentId"]
    display = state["prefix"] + " FNOL V2 Pilot"
    if not target.get("teamId") and not target.get("teamOperation"):
        groups = graph.call(
            "GET", "/v1.0/groups?$filter=displayName eq '" + display
            + "'&$select=id,description,visibility",
        )["value"]
        if groups:
            if len(groups) != 1 or groups[0].get("description") != marker:
                raise ValueError("An existing pilot-name group does not match v2 ownership.")
            if groups[0].get("visibility") != "Private":
                raise ValueError("The notification Team must remain private.")
            owners = graph.call("GET", "/v1.0/groups/" + groups[0]["id"] + "/owners?$select=id")["value"]
            if {owner["id"] for owner in owners} != {state["operatorId"]}:
                raise ValueError("Only the pilot operator may own the isolated notification Team.")
            target["teamId"] = groups[0]["id"]
        else:
            response = requests.post(
                "https://graph.microsoft.com/v1.0/teams",
                headers={"Authorization": "Bearer " + credential.get_token(
                    "https://graph.microsoft.com/.default",
                ).token},
                json={
                    "template@odata.bind": "https://graph.microsoft.com/v1.0/teamsTemplates('standard')",
                    "displayName": display, "description": marker, "visibility": "Private",
                    "firstChannelName": "Synthetic intake",
                    "members": [{
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                        "roles": ["owner"],
                        "user@odata.bind": "https://graph.microsoft.com/v1.0/users('"
                        + state["operatorId"] + "')",
                    }],
                }, timeout=(15, 120), allow_redirects=False,
            )
            if response.status_code != 202:
                raise RuntimeError(f"Pilot Team creation failed: HTTP {response.status_code}.")
            operation = response.headers.get("Location", "")
            if not re.fullmatch(r"/teams\('[0-9a-f-]{36}'\)/operations\('[0-9a-f-]{36}'\)", operation):
                raise ValueError("Team creation did not return a trusted operation path.")
            target["teamOperation"] = "/v1.0" + operation
        save(state)
    if target.get("teamOperation"):
        for _ in range(60):
            operation = graph.call("GET", target["teamOperation"])
            if operation["status"] == "succeeded":
                target["teamId"] = str(uuid.UUID(operation["targetResourceId"]))
                del target["teamOperation"]
                save(state)
                break
            if operation["status"] == "failed":
                raise RuntimeError("The isolated Team creation operation failed.")
            time.sleep(5)
        else:
            raise TimeoutError("Pilot Team provisioning is pending; rerun notifications to resume.")
    team = graph.call("GET", "/v1.0/teams/" + target["teamId"])
    if team.get("description") != marker or team.get("visibility", "").lower() != "private":
        raise ValueError("Notification Team ownership/privacy readback does not match.")
    channels = graph.call("GET", "/v1.0/teams/" + target["teamId"] + "/channels")["value"]
    channel = [item for item in channels if item["displayName"] == "Synthetic intake"]
    if len(channel) != 1:
        raise ValueError("The dedicated synthetic-intake channel was not found uniquely.")
    target["channelId"] = channel[0]["id"]
    target["privateTeamVerified"] = True
    save(state)


def notification_connected(connection, user_principal_name):
    properties = connection["properties"]
    if not any(item.get("status") == "Connected" for item in properties.get("statuses", [])):
        return False
    actual = properties.get("authenticatedUser", {}).get("name", "")
    if actual.casefold() != user_principal_name.casefold():
        raise ValueError("The Teams connector is authorized as a different user.")
    return True


def configure_notifications(state):
    if not state.get("teams", {}).get("identityConfigured"):
        raise ValueError("Configure the personal Teams application before notifications.")
    credential, arm, graph = clients(state)
    operator = graph.call("GET", "/v1.0/me?$select=id,userPrincipalName")
    if operator["id"] != state["operatorId"]:
        raise ValueError("Notification setup must use the approved pilot operator.")
    ensure_pilot_team(state, credential, graph)
    target = state["notifications"]
    base = (f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
            "/providers/")
    connection_id = base + "Microsoft.Web/connections/" + state["prefix"] + "-notify-teams"
    connection_path = connection_id + "?api-version=2016-06-01"
    connection = arm.call("GET", connection_path, missing_ok=True)
    if connection is None:
        connection = arm.call("PUT", connection_path, {
            "location": state["location"],
            "tags": {"environmentId": state["environmentId"], "solution": "fnol-delegated-v2"},
            "properties": {
                "displayName": "V2 synthetic notification only",
                "api": {"id": (
                    f"/subscriptions/{state['subscriptionId']}/providers/Microsoft.Web/locations/"
                    + state["location"] + "/managedApis/teams"
                )},
            },
        })
    if connection.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The Teams connection is not owned by this v2 environment.")
    connected = notification_connected(connection, operator["userPrincipalName"])
    target.update(connectionId=connection_id, connectionAuthorized=connected)
    save(state)
    print("Private Teams notification connection verified." if connected else
          "Authorize the owned Teams connection as the pilot operator, then rerun notifications. "
          "Email intake has not been enabled.")
