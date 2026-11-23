"""Individual Work IQ OAuth setup; never an application-only grounding token."""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from v2.deploy import save
from v2.provision import Rest, clients

WORKIQ_APP_ID = "fdcc1f02-fc51-4226-8753-f668596af7f7"
WORKIQ_SCOPE = "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask"
WORKIQ_ENDPOINT = "https://workiq.svc.cloud.microsoft/mcp"


def prepare_billing_audience(state):
    if state.get("workiq", {}).get("enablementApproved") is not True:
        raise ValueError("Work IQ pilot enablement must be explicitly approved.")
    _, _, graph = clients(state)
    name = state["prefix"] + " Work IQ Pilot"
    marker = "fnol-delegated-v2:" + state["environmentId"] + ":workiq-billing"
    groups = graph.call(
        "GET", "/v1.0/groups?$filter=" + quote("displayName eq '" + name + "'", safe="")
        + "&$select=id,description,securityEnabled,mailEnabled",
    )["value"]
    if groups:
        if (len(groups) != 1 or groups[0].get("description") != marker
                or groups[0].get("securityEnabled") is not True or groups[0].get("mailEnabled")):
            raise ValueError("The billing audience group does not match the owned security group.")
        group = groups[0]
    else:
        user = "https://graph.microsoft.com/v1.0/users/" + str(uuid.UUID(state["operatorId"]))
        group = graph.call("POST", "/v1.0/groups", {
            "displayName": name, "description": marker,
            "securityEnabled": True, "mailEnabled": False,
            "mailNickname": state["prefix"] + "workiqpilot", "groupTypes": [],
            "owners@odata.bind": [user], "members@odata.bind": [user],
        })
    members = graph.call("GET", "/v1.0/groups/" + group["id"] + "/members?$select=id")["value"]
    if {member["id"] for member in members} != {state["operatorId"]}:
        raise ValueError("The Work IQ billing audience must contain only the approved pilot operator.")
    state["workiq"]["billingAudience"] = {"groupId": group["id"], "displayName": name}
    save(state)
    print("Pilot-only Work IQ billing audience verified; no billing policy was activated.")


def provision_workiq(state):
    credential, arm, graph = clients(state)
    workiq = state["workiq"]
    if workiq.get("enablementApproved") is not True:
        raise ValueError("Work IQ tenant enablement must be explicitly approved.")
    resource = graph.call(
        "GET", "/v1.0/servicePrincipals/" + str(uuid.UUID(workiq["resourcePrincipalId"])),
    )
    scopes = [scope for scope in resource["oauth2PermissionScopes"]
              if scope["value"] == "WorkIQAgent.Ask" and scope["isEnabled"]]
    if resource["appId"] != WORKIQ_APP_ID or len(scopes) != 1:
        raise ValueError("The live Work IQ resource/scope does not match Microsoft's documented API.")
    name = state["prefix"] + "-workiq-oauth"
    application = graph.call(
        "GET", "/v1.0/applications?$filter=" + quote(f"displayName eq '{name}'", safe=""),
    )["value"]
    if application:
        if len(application) != 1 or state["environmentId"] not in application[0].get("tags", []):
            raise ValueError("Refusing to adopt an unrelated Work IQ OAuth application.")
        application = application[0]
    else:
        application = graph.call("POST", "/v1.0/applications", {
            "displayName": name, "signInAudience": "AzureADMyOrg",
            "tags": ["fnol-delegated-v2", state["environmentId"]],
            "requiredResourceAccess": [{
                "resourceAppId": resource["appId"],
                "resourceAccess": [{"id": scopes[0]["id"], "type": "Scope"}],
            }],
            "web": {
                "redirectUris": [],
                "implicitGrantSettings": {"enableAccessTokenIssuance": False, "enableIdTokenIssuance": False},
            },
        })
    identity = workiq.setdefault("identity", {})
    identity.update({"objectId": application["id"], "clientId": application["appId"]})
    save(state)
    principals = graph.call(
        "GET", "/v1.0/servicePrincipals?$filter=" + quote(f"appId eq '{identity['clientId']}'", safe=""),
    )["value"]
    if len(principals) > 1:
        raise ValueError("Ambiguous Work IQ OAuth service principal.")
    principal = principals[0] if principals else graph.call(
        "POST", "/v1.0/servicePrincipals", {"appId": identity["clientId"]},
    )
    identity["principalId"] = principal["id"]
    selection = state["vaultSelection"]
    secret_name = selection["secretPrefix"] + "-workiq-client-secret"
    scope = selection["id"] + "/secrets/" + secret_name
    role = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
    assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}|{state['operatorId']}|{role}"))
    arm.call(
        "PUT", scope + "/providers/Microsoft.Authorization/roleAssignments/"
        + assignment + "?api-version=2022-04-01", {"properties": {
            "principalId": state["operatorId"], "principalType": "User",
            "roleDefinitionId": f"/subscriptions/{state['subscriptionId']}"
            + "/providers/Microsoft.Authorization/roleDefinitions/" + role,
        }},
    )
    vault = Rest(credential, selection["url"], "https://vault.azure.net/.default")
    path = "/secrets/" + secret_name + "?api-version=7.4"
    secret = vault.call("GET", path, missing_ok=True, sensitive=True)
    if secret is not None and (
        secret.get("tags", {}).get("environmentId") != state["environmentId"]
        or secret.get("tags", {}).get("clientId") != identity["clientId"]
    ):
        raise ValueError("Refusing to use another application's Work IQ client secret.")
    if secret is None:
        if identity.get("pendingSecretId"):
            graph.call(
                "POST", f"/v1.0/applications/{identity['objectId']}/removePassword",
                {"keyId": identity["pendingSecretId"]}, sensitive=True,
            )
        password = graph.call(
            "POST", f"/v1.0/applications/{identity['objectId']}/addPassword",
            {"passwordCredential": {
                "displayName": "v2-workiq-90-days",
                "endDateTime": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
            }}, sensitive=True,
        )
        identity["pendingSecretId"] = password["keyId"]
        save(state)
        secret = vault.call("PUT", path, {
            "value": password["secretText"], "tags": {
                "environmentId": state["environmentId"], "solution": "fnol-delegated-v2",
                "clientId": identity["clientId"],
            },
        }, sensitive=True)
        identity["secretExpiresAt"] = password["endDateTime"]
        del identity["pendingSecretId"]
        save(state)
    grants = graph.call(
        "GET", "/v1.0/oauth2PermissionGrants?$filter=" + quote(
            f"clientId eq '{principal['id']}' and resourceId eq '{resource['id']}'", safe="",
        ),
    )["value"]
    if grants:
        if (len(grants) != 1 or grants[0]["consentType"] != "AllPrincipals"
                or set(grants[0]["scope"].split()) != {"WorkIQAgent.Ask"}):
            raise ValueError("Existing Work IQ consent does not match the approved delegated scope.")
    else:
        grants = [graph.call("POST", "/v1.0/oauth2PermissionGrants", {
            "clientId": principal["id"], "consentType": "AllPrincipals",
            "resourceId": resource["id"], "scope": "WorkIQAgent.Ask",
        })]
    workiq["adminConsentGrantId"] = grants[0]["id"]
    save(state)
    tenant_origin = "https://login.microsoftonline.com/" + state["tenantId"] + "/oauth2/v2.0/"
    connection_path = (
        state["azure"]["projectId"] + "/connections/fnol-v2-workiq?api-version=2025-10-01-preview"
    )
    connection = arm.call("PUT", connection_path, {"properties": {
        "authType": "OAuth2", "category": "RemoteTool", "target": WORKIQ_ENDPOINT,
        "credentials": {"clientId": identity["clientId"], "clientSecret": secret["value"]},
        "authorizationUrl": tenant_origin + "authorize", "tokenUrl": tenant_origin + "token",
        "refreshUrl": tenant_origin + "token", "scopes": [WORKIQ_SCOPE, "offline_access"],
        "isSharedToAll": True, "metadata": {"type": "generic_mcp"},
    }}, sensitive=True)
    workiq["pendingConnectionId"] = connection["id"]
    save(state)
    properties = arm.call("GET", connection_path, sensitive=True)["properties"]
    if (properties.get("authType") != "OAuth2" or properties.get("target") != WORKIQ_ENDPOINT
            or properties.get("scopes") != [WORKIQ_SCOPE, "offline_access"]):
        raise RuntimeError("The service did not retain the required individual Work IQ OAuth settings.")
    redirect = properties.get("redirectUrl")
    parsed = urlparse(redirect or "")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.port
            or parsed.hostname != "global.consent.azure-apim.net"
            or not parsed.path.startswith("/redirect/")):
        raise RuntimeError(
            "The service did not return a trusted OAuth redirect URI. Connection property names: "
            + ", ".join(sorted(properties))
        )
    graph.call("PATCH", "/v1.0/applications/" + identity["objectId"], {
        "web": {"redirectUris": [redirect]},
    })
    workiq["redirectUri"] = redirect
    workiq["connectionId"] = workiq.pop("pendingConnectionId")
    save(state)
    print("Work IQ individual OAuth connection and its dedicated application are configured.")
