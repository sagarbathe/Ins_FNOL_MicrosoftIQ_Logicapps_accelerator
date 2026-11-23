"""Pilot-user-only delegated Mail.Send consent for confirmed escalation."""

import copy
from urllib.parse import quote

from v2.deploy import save
from v2.provision import clients

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"


def configure_mail_identity(state):
    _, _, graph = clients(state)
    identity = state["webIdentity"]
    path = "/v1.0/applications/" + identity["objectId"]
    application = graph.call("GET", path)
    if (application.get("appId") != identity["clientId"]
            or state["environmentId"] not in application.get("tags", [])):
        raise ValueError("The delegated mail application is not the owned v2 web identity.")
    resources = graph.call(
        "GET", "/v1.0/servicePrincipals?$filter=" + quote("appId eq '" + GRAPH_APP_ID + "'", safe="")
        + "&$select=id,appId,oauth2PermissionScopes",
    )["value"]
    if len(resources) != 1:
        raise ValueError("Microsoft Graph must resolve uniquely in the configured tenant.")
    resource = resources[0]
    scopes = [scope for scope in resource["oauth2PermissionScopes"]
              if scope["value"] == "Mail.Send" and scope.get("isEnabled")]
    if len(scopes) != 1:
        raise ValueError("The official delegated Mail.Send permission was not found uniquely.")
    required = copy.deepcopy(application.get("requiredResourceAccess", []))
    matches = [item for item in required if item["resourceAppId"] == GRAPH_APP_ID]
    if len(matches) > 1:
        raise ValueError("Review duplicate Microsoft Graph permission declarations before adding Mail.Send.")
    entry = matches[0] if matches else {"resourceAppId": GRAPH_APP_ID, "resourceAccess": []}
    permission = {"id": scopes[0]["id"], "type": "Scope"}
    if permission not in entry["resourceAccess"]:
        entry["resourceAccess"].append(permission)
        if not matches:
            required.append(entry)
        graph.call("PATCH", path, {"requiredResourceAccess": required})
    for user in state["teams"]["pilotUserIds"]:
        grant_path = "/v1.0/oauth2PermissionGrants"
        grants = graph.call("GET", grant_path + "?$filter=" + quote(
            "clientId eq '" + identity["principalId"] + "' and resourceId eq '" + resource["id"] + "'",
            safe="",
        ))["value"]
        if any(grant["consentType"] == "AllPrincipals" and "Mail.Send" in grant["scope"].split()
               for grant in grants):
            raise ValueError("Tenant-wide Mail.Send consent exists unexpectedly; review rather than expand it.")
        matches = [grant for grant in grants
                   if grant["consentType"] == "Principal" and grant.get("principalId") == user]
        if len(matches) > 1:
            raise ValueError("Pilot delegated mail consent is ambiguous.")
        if matches:
            grant = matches[0]
            merged = set(grant["scope"].split()) | {"Mail.Send"}
            if merged != set(grant["scope"].split()):
                graph.call("PATCH", grant_path + "/" + grant["id"], {"scope": " ".join(sorted(merged))})
        else:
            grant = graph.call("POST", grant_path, {
                "clientId": identity["principalId"], "resourceId": resource["id"],
                "consentType": "Principal", "principalId": user, "scope": "Mail.Send",
            })
        actual = graph.call("GET", grant_path + "/" + grant["id"])
        if (actual["consentType"] != "Principal" or actual["principalId"] != user
                or actual["clientId"] != identity["principalId"] or actual["resourceId"] != resource["id"]
                or "Mail.Send" not in actual["scope"].split()):
            raise RuntimeError("The per-pilot delegated Mail.Send consent failed readback.")
    state["escalation"] = {
        "identityConfigured": True, "permission": "delegated Mail.Send",
        "pilotUserIds": state["teams"]["pilotUserIds"], "tenantWideGrantCreated": False,
        "sharedMailboxFallback": False, "endToEndVerified": False,
    }
    save(state)
    print("Delegated Mail.Send consent verified for only the named pilot users; no email was sent.")
