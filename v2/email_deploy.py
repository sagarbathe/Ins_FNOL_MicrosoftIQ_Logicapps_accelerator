"""Deploy isolated email processing without modifying protected workflows or enabling mailbox routing."""

import hashlib
import copy
import json
import time
import uuid

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

from v2.deploy import save
from v2.email_processing import TRIAGE_INSTRUCTIONS
from v2.mail_source import connector_folder_id, verify_connection, verify_protected_workflows
from v2.provision import clients, configure_app
from v2.notifications import notification_connected
from v2.recovery import RECOVERY_ACTIONS


def canonical_definition(definition):
    result = copy.deepcopy(definition)
    for trigger in result.get("triggers", {}).values():
        evaluated = trigger.pop("evaluatedRecurrence", None)
        if evaluated is not None and evaluated != trigger.get("recurrence"):
            raise ValueError("The evaluated trigger recurrence differs from the intended schedule.")
    return result


def configure_screening_agent(state, credential):
    definition = PromptAgentDefinition(
        model="investigation", instructions=TRIAGE_INSTRUCTIONS, tools=[],
        text={"format": {"type": "json_object"}},
    )
    digest = hashlib.sha256(json.dumps(definition.as_dict(), sort_keys=True).encode()).hexdigest()
    with AIProjectClient(endpoint=state["azure"]["projectEndpoint"], credential=credential) as project:
        agent = state.get("emailTriageAgent", {})
        if agent.get("definitionSha256") != digest:
            version = project.agents.create_version(
                agent_name="fnol-v2-email-screening", definition=definition,
                description="Unverified synthetic email screening only. No IQ tools or write tools.",
            )
            agent = {"name": version.name, "version": version.version, "definitionSha256": digest}
            state["emailTriageAgent"] = agent
            save(state)
        if project.agents.get_version(
            agent_name=agent["name"], agent_version=agent["version"],
        ).definition.as_dict() != definition.as_dict():
            raise RuntimeError("The screening agent differs from its tool-free, JSON-output pin.")


def update_screening_agent(state):
    credential, arm, _ = clients(state)
    project = arm.call("GET", state["azure"]["projectId"] + "?api-version=2025-10-01-preview")
    if project.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The screening project is not owned by v2.")
    verify_protected_workflows(state, arm)
    configure_screening_agent(state, credential)
    configure_app(state, arm)
    print("Dedicated screening agent JSON format pinned at agent level; backend settings updated.")


def configure_recovery(state, arm, workflow_id, principal_id):
    subscription = "/subscriptions/" + state["subscriptionId"]
    group = subscription + "/resourceGroups/" + state["resourceGroup"]
    name = str(uuid.uuid5(uuid.NAMESPACE_URL, state["environmentId"] + "/email-recovery-role"))
    role_id = subscription + "/providers/Microsoft.Authorization/roleDefinitions/" + name
    description = "Owned FNOL v2 recovery only: " + state["environmentId"]
    existing = arm.call("GET", role_id + "?api-version=2022-04-01", missing_ok=True)
    if existing and existing["properties"].get("description") != description:
        raise ValueError("The deterministic recovery role is not owned by this environment.")
    properties = {
        "roleName": state["prefix"] + " email recovery", "description": description,
        "type": "CustomRole", "assignableScopes": [group],
        "permissions": [{"actions": RECOVERY_ACTIONS, "notActions": [],
                         "dataActions": [], "notDataActions": []}],
    }
    arm.call("PUT", role_id + "?api-version=2022-04-01", {"properties": properties})
    actual = arm.call("GET", role_id + "?api-version=2022-04-01")["properties"]
    if (actual["assignableScopes"] != [group] or actual["permissions"] != properties["permissions"]
            or actual["description"] != description):
        raise RuntimeError("The narrow recovery role differs from its reviewed permissions.")
    assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, workflow_id + principal_id + role_id))
    path = workflow_id + "/providers/Microsoft.Authorization/roleAssignments/" + assignment
    arm.call("PUT", path + "?api-version=2022-04-01", {"properties": {
        "principalId": principal_id, "principalType": "ServicePrincipal", "roleDefinitionId": role_id,
    }})
    assigned = arm.call("GET", path + "?api-version=2022-04-01")["properties"]
    if assigned["principalId"] != principal_id or assigned["scope"].casefold() != workflow_id.casefold():
        raise RuntimeError("Recovery permissions were not verified at only the owned workflow scope.")
    state["emailIntake"]["recoveryRoleId"] = role_id
    save(state)


def workflow_definition():
    def private_http(path, body, run_after=None):
        return {
            "type": "Http", "runAfter": run_after or {},
            "runtimeConfiguration": {"secureData": {"properties": ["inputs", "outputs"]}},
            "inputs": {
                "uri": "@concat(parameters('webUrl'), " + path + ")",
                "method": "POST", "headers": {"Content-Type": "application/json"},
                "authentication": {"type": "ManagedServiceIdentity",
                                   "audience": "@parameters('webAudience')"},
                "retryPolicy": {"type": "none"}, "body": body,
            },
        }

    register = private_http("'/api/intake/email'", {
        "mailbox": "@parameters('mailbox')", "folder_id": "@parameters('folderId')",
        "internet_message_id": "@triggerBody()['internetMessageId']",
        "subject": "@triggerBody()['subject']", "body": "@triggerBody()['body']",
        "is_html": "@triggerBody()['isHtml']", "sender": "@triggerBody()['from']",
        "received_at": "@triggerBody()['receivedDateTime']",
        "workflow_run_id": "@workflow()['run']['name']",
    })
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4", "body": [{
            "type": "TextBlock", "text": "Synthetic email case ready: @{body('Register_and_screen')['case_id']}",
            "wrap": True,
        }, {
            "type": "TextBlock", "text": (
                "Open your private investigation inside Teams. "
                "No email content, initial screening or private answers are posted here."
            ), "wrap": True,
        }],
        "actions": [{"type": "Action.OpenUrl", "title": "Investigate privately",
                     "url": "@{body('Register_and_screen')['teams_link']}"}],
    }
    notify = {
        "type": "ApiConnection", "runAfter": {},
        "inputs": {
            "host": {"connection": {"name": "@parameters('$connections')['teams']['connectionId']"}},
            "method": "post",
            "path": "/v1.0/teams/conversation/adaptivecard/poster/User/location/Channel",
            "retryPolicy": {"type": "none"}, "body": {
                "recipient": {"groupId": "@parameters('teamId')", "channelId": "@parameters('channelId')"},
                "messageBody": json.dumps(card, separators=(",", ":")),
            },
        },
    }
    acknowledge = private_http(
        "'/api/intake/email/', body('Register_and_screen')['case_id'], '/notified'",
        {
            "workflow_run_id": "@workflow()['run']['name']",
            "notification_id": "@body('Register_and_screen')['notification_id']",
            "message_id": "@body('Post_minimal_card')['id']",
        }, {"Post_minimal_card": ["Succeeded"]},
    )
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            **{name: {"type": "String"} for name in (
                "webUrl", "webAudience", "mailbox", "folderId", "subjectTag", "teamId", "channelId",
                "operatorEmail", "connectorFolderId",
            )}, "$connections": {"type": "Object"},
        },
        "triggers": {"New_pilot_email": {
            "type": "ApiConnection", "splitOn": "@triggerBody()?['value']",
            "recurrence": {"frequency": "Minute", "interval": 1},
            "runtimeConfiguration": {"secureData": {"properties": ["inputs", "outputs"]}},
            "inputs": {
                "host": {"connection": {"name": "@parameters('$connections')['office365']['connectionId']"}},
                "method": "get", "path": "/v3/Mail/OnNewEmail",
                "queries": {
                    "folderPath": "@parameters('connectorFolderId')", "subjectFilter": "@parameters('subjectTag')",
                    "includeAttachments": False, "fetchOnlyWithAttachment": False,
                },
            },
        }},
        "actions": {
            "Process_email": {"type": "Scope", "runAfter": {}, "actions": {
                "Register_and_screen": register,
                "If_notification_required": {
                    "type": "If", "runAfter": {"Register_and_screen": ["Succeeded"]},
                    "expression": "@equals(body('Register_and_screen')['notify'], true)",
                    "actions": {"Post_minimal_card": notify, "Record_notification": acknowledge},
                    "else": {"actions": {}},
                },
            }},
            "Alert_operator": {
                "type": "ApiConnection", "runAfter": {"Process_email": ["Failed", "TimedOut"]},
                "runtimeConfiguration": {"secureData": {"properties": ["inputs", "outputs"]}},
                "inputs": {
                    "host": {"connection": {"name": "@parameters('$connections')['office365']['connectionId']"}},
                    "method": "post", "path": "/v2/Mail", "retryPolicy": {"type": "none"},
                    "body": {
                        "To": "@parameters('operatorEmail')",
                        "Subject": "FNOL v2 pilot processing needs reconciliation",
                        "Body": (
                            "<p>An isolated synthetic email run failed or timed out.</p>"
                            "<p>Run: @{workflow()['run']['name']}</p>"
                            "<p>Inspect private case processing before any replay. A notification "
                            "might already have been delivered. Do not blindly resubmit.</p>"
                            "<p>No private email or investigation content is included in this alert.</p>"
                        ),
                        "Importance": "High",
                    },
                },
            },
            "Fail_processing": {
                "type": "Terminate",
                "runAfter": {"Alert_operator": ["Succeeded", "Failed", "TimedOut"]},
                "inputs": {"runStatus": "Failed", "runError": {
                    "code": "EmailProcessingRequiresReconciliation",
                    "message": "Inspect the private case and delivery outcome before retrying.",
                }},
            },
        },
    }


def provision_email(state):
    source = state.get("emailIntake", {})
    if not source.get("sourceConfigured") or source.get("ruleEnabled") or source.get("workflowEnabled"):
        raise ValueError("A verified, disabled, isolated mailbox source is required.")
    if not state.get("teams", {}).get("pilotAvailabilityVerified") or not state["teams"].get("catalogId"):
        raise ValueError("Verified pilot-only Teams availability and catalog identity are required.")
    credential, arm, graph = clients(state)
    verify_protected_workflows(state, arm)
    email_connection = verify_connection(state, arm)
    source["connectorFolderId"] = connector_folder_id(state, credential, arm, email_connection)
    save(state)
    operator = graph.call("GET", "/v1.0/me?$select=id,userPrincipalName")
    if operator["id"] != state["operatorId"]:
        raise ValueError("Deploy using the approved native pilot operator.")
    project_resource = arm.call("GET", state["azure"]["projectId"] + "?api-version=2025-10-01-preview")
    if project_resource.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The initial screening project is not owned by v2.")
    configure_screening_agent(state, credential)
    base = (f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
            "/providers/")
    workflow_id = base + "Microsoft.Logic/workflows/" + state["prefix"] + "-email-intake"
    existing = arm.call("GET", workflow_id + "?api-version=2019-05-01", missing_ok=True)
    if existing and existing.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("Refusing to overwrite an unowned workflow.")
    target = state["notifications"]
    teams_connection = arm.call("GET", target["connectionId"] + "?api-version=2016-06-01")
    if (teams_connection.get("tags", {}).get("environmentId") != state["environmentId"]
            or not notification_connected(teams_connection, operator["userPrincipalName"])):
        raise ValueError("The isolated Teams notification connector is not authorized as the operator.")
    parameters = {
        "webUrl": state["azure"]["webUrl"], "webAudience": state["teams"]["resourceUri"],
        "mailbox": source["mailbox"], "folderId": source["folderId"],
        "connectorFolderId": source["connectorFolderId"],
        "subjectTag": source["subjectTag"], "teamId": target["teamId"],
        "channelId": target["channelId"], "operatorEmail": operator["userPrincipalName"],
        "$connections": {
            name: {"connectionId": resource["id"], "connectionName": resource["name"],
                   "id": resource["properties"]["api"]["id"]}
            for name, resource in (("office365", email_connection), ("teams", teams_connection))
        },
    }
    definition = workflow_definition()
    resource = arm.call("PUT", workflow_id + "?api-version=2019-05-01", {
        "location": state["location"],
        "tags": {"environmentId": state["environmentId"], "solution": "fnol-delegated-v2"},
        "identity": {"type": "SystemAssigned"}, "properties": {
            "state": "Disabled", "definition": definition,
            "parameters": {name: {"value": value} for name, value in parameters.items()},
        },
    })
    actual = arm.call("GET", workflow_id + "?api-version=2019-05-01")
    if (actual["properties"]["state"] != "Disabled"
            or canonical_definition(actual["properties"]["definition"]) != definition
            or actual["properties"]["parameters"] != {
                name: {"value": value} for name, value in parameters.items()
            }):
        raise RuntimeError("The disabled email workflow differs from its exact intended definition.")
    principal_id = resource["identity"]["principalId"]
    for _ in range(12):
        principal = graph.call(
            "GET", "/v1.0/servicePrincipals/" + principal_id + "?$select=id,appId", missing_ok=True,
        )
        if principal:
            break
        time.sleep(5)
    else:
        raise RuntimeError("The isolated workflow identity is not yet available; routing stays disabled.")
    web_id = base + "Microsoft.Web/sites/" + state["azure"]["webName"]
    web = arm.call("GET", web_id + "?api-version=2023-12-01")
    if web.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The private intake web resource is not owned by v2.")
    project_id = state["azure"]["projectId"]
    web_principal = web["identity"]["principalId"]
    role = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
    assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, project_id + web_principal + role))
    arm.call("PUT", project_id + "/providers/Microsoft.Authorization/roleAssignments/"
             + assignment + "?api-version=2022-04-01", {"properties": {
                 "principalId": web_principal, "principalType": "ServicePrincipal",
                 "roleDefinitionId": f"/subscriptions/{state['subscriptionId']}"
                 "/providers/Microsoft.Authorization/roleDefinitions/" + role,
             }})
    source.update(
        workflowId=workflow_id, workflowPrincipalId=principal_id, workflowClientId=principal["appId"],
        assignedUsers=state["teams"]["pilotUserIds"],
        workflowDefinitionSha256=hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest(),
        workflowDeployed=True, workflowEnabled=False, backendConfigured=True,
    )
    save(state)
    configure_recovery(state, arm, workflow_id, web_principal)
    configure_app(state, arm)
    verify_protected_workflows(state, arm)
    print("Email workflow and tool-free initial screening deployed disabled; backend configured.")


def rebind_email_folder(state):
    source = state["emailIntake"]
    credential, arm, _ = clients(state)
    verify_protected_workflows(state, arm)
    connection = verify_connection(state, arm)
    path = source["workflowId"] + "?api-version=2019-05-01"
    existing = arm.call("GET", path)
    definition = canonical_definition(existing["properties"]["definition"])
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    if (existing.get("tags", {}).get("environmentId") != state["environmentId"]
            or digest != source["workflowDefinitionSha256"]):
        raise ValueError("The owned workflow differs from its last verified definition.")
    picker_id = connector_folder_id(state, credential, arm, connection)
    parameters = existing["properties"]["parameters"]
    if (parameters["folderId"]["value"] != source["folderId"]
            or parameters["mailbox"]["value"] != source["mailbox"]):
        raise ValueError("The approved private mailbox source changed.")
    parameters["connectorFolderId"] = {"value": picker_id}
    arm.call("POST", source["workflowId"] + "/disable?api-version=2019-05-01")
    source["workflowEnabled"] = False
    save(state)
    intended = workflow_definition()
    arm.call("PUT", path, {
        "location": existing["location"], "tags": existing["tags"],
        "identity": {"type": "SystemAssigned"},
        "properties": {"state": "Disabled", "definition": intended, "parameters": parameters},
    })
    actual = arm.call("GET", path)
    if (canonical_definition(actual["properties"]["definition"]) != intended
            or actual["properties"]["parameters"] != parameters
            or actual["identity"]["principalId"] != existing["identity"]["principalId"]
            or actual["properties"]["state"] != "Disabled"):
        raise RuntimeError("The connector folder binding or retained workflow identity differs.")
    source.update(
        connectorFolderId=picker_id,
        workflowDefinitionSha256=hashlib.sha256(json.dumps(intended, sort_keys=True).encode()).hexdigest(),
    )
    save(state)
    arm.call("POST", source["workflowId"] + "/enable?api-version=2019-05-01")
    if arm.call("GET", path)["properties"]["state"] != "Enabled":
        raise RuntimeError("The corrected isolated watcher was not re-enabled.")
    source["workflowEnabled"] = True
    verify_protected_workflows(state, arm)
    save(state)
    print("Watcher rebound to the exact connector-picked folder ID; original probe retained, no second send.")
