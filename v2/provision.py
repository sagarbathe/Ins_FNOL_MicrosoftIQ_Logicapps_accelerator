"""Provisioning helpers for the new environment, never the legacy deployment."""

import zipfile
import ipaddress
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import requests
from azure.identity import AzureCliCredential
from cryptography.fernet import Fernet

from v2.deploy import ROOT, STATE, az, save
from v2 import RUNTIME_MODULES, runtime_digest


class Rest:
    def __init__(self, credential, origin, scope):
        self.credential = credential
        self.origin = origin.rstrip("/")
        self.scope = scope

    def call(self, method, path, body=None, *, missing_ok=False, sensitive=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("An API-relative path is required.")
        response = requests.request(
            method, self.origin + path, json=body,
            headers={"Authorization": "Bearer " + self.credential.get_token(self.scope).token},
            timeout=(15, 120), allow_redirects=False,
        )
        if missing_ok and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            detail = "" if sensitive else response.text[:1500]
            raise RuntimeError(f"{urlparse(self.origin).hostname}: HTTP {response.status_code}. {detail}")
        return response.json() if response.content else {}


def clients(state):
    credential = AzureCliCredential(tenant_id=state["tenantId"])
    return (
        credential,
        Rest(credential, "https://management.azure.com", "https://management.azure.com/.default"),
        Rest(credential, "https://graph.microsoft.com", "https://graph.microsoft.com/.default"),
    )


def public_ipv4_cidr(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global:
        raise ValueError("Only individual public IPv4 addresses can be allowed.")
    return str(address) + "/32"


def configure_vault_network(state, *, approved=False):
    if not approved:
        raise RuntimeError("Explicit approval of restricted public access is required.")
    if state.get("vaultNetwork", {}).get("status") == "blocked-by-policy":
        raise RuntimeError("Tenant policy blocks public vault access; administrator review is required.")
    credential, arm, _ = clients(state)
    base = (f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
            "/providers/")
    vault_path = base + f"Microsoft.KeyVault/vaults/{state['prefix']}-vault?api-version=2023-07-01"
    vault = arm.call("GET", vault_path)
    if (vault.get("tags", {}).get("environmentId") != state["environmentId"]
            or vault["properties"].get("enableRbacAuthorization") is not True):
        raise RuntimeError("Refusing to change an unowned or non-RBAC vault.")
    site = arm.call(
        "GET", base + f"Microsoft.Web/sites/{state['azure']['webName']}?api-version=2023-12-01",
    )
    outbound = site["properties"].get("outboundIpAddresses", "")
    cidrs = sorted({public_ipv4_cidr(value.strip()) for value in outbound.split(",") if value.strip()})
    if not cidrs:
        raise RuntimeError("Azure did not return the app's public outbound IPs.")
    if state.get("vaultNetwork", {}).get("deployerCidr"):
        cidrs.append(state["vaultNetwork"]["deployerCidr"])

    def apply():
        arm.call("PATCH", vault_path, {"properties": {
            "publicNetworkAccess": "Enabled",
            "networkAcls": {
                "defaultAction": "Deny", "bypass": "None",
                "ipRules": [{"value": value} for value in sorted(set(cidrs))],
                "virtualNetworkRules": [],
            },
        }})
        state["vaultNetwork"] = {
            "mode": "selected-public-ips", "cidrs": sorted(set(cidrs)),
            "defaultAction": "Deny", "bypass": "None",
            "deployerCidr": state.get("vaultNetwork", {}).get("deployerCidr"),
            "status": "unverified",
        }
        save(state)
        actual = arm.call("GET", vault_path)["properties"]
        if actual.get("publicNetworkAccess") != "Enabled":
            state["vaultNetwork"]["status"] = "blocked"
            state["vaultNetwork"]["actualPublicNetworkAccess"] = actual.get("publicNetworkAccess")
            save(state)
            raise RuntimeError("Azure kept public vault access disabled; stop and review tenant policy.")
        rules = actual.get("networkAcls", {})
        if (rules.get("defaultAction") != "Deny" or rules.get("bypass") != "None"
                or {rule["value"] for rule in rules.get("ipRules", [])} != set(cidrs)):
            arm.call("PATCH", vault_path, {"properties": {"publicNetworkAccess": "Disabled"}})
            state["vaultNetwork"]["status"] = "blocked"
            save(state)
            raise RuntimeError("Firewall readback did not match; public access was disabled for safety.")

    apply()
    url = state["azure"]["vaultUrl"].rstrip("/") + "/secrets/token-encryption-key?api-version=7.4"
    headers = {"Authorization": "Bearer " + credential.get_token("https://vault.azure.net/.default").token}
    response = requests.get(url, headers=headers, timeout=(15, 60), allow_redirects=False)
    if response.status_code == 403:
        error = response.json().get("error", {})
        message = error.get("message", "")
        client = re.search(r"Client address:\s*([0-9.]+)", message)
        if (error.get("innererror", {}).get("code") != "ForbiddenByFirewall"
                or not client or f"oid={state['operatorId']}".lower() not in message.lower()):
            raise RuntimeError("Vault access is still denied; no additional network grants were made.")
        cidr = public_ipv4_cidr(client.group(1))
        state["vaultNetwork"]["deployerCidr"] = cidr
        cidrs.append(cidr)
        apply()
        response = requests.get(url, headers=headers, timeout=(15, 60), allow_redirects=False)
    if response.status_code not in (200, 404):
        raise RuntimeError(f"Restricted vault access is not verified: HTTP {response.status_code}.")
    state["vaultNetwork"]["status"] = "verified"
    save(state)
    print("Vault access verified with app/deployment IPs only, default-deny and RBAC preserved.")


def select_vault(state, name, arm):
    resource = az("keyvault", "show", "--name", name)
    if (resource["properties"]["tenantId"] != state["tenantId"]
            or not resource["id"].lower().startswith(
                f"/subscriptions/{state['subscriptionId']}/".lower())
            or resource["properties"].get("enableRbacAuthorization") is not True):
        raise RuntimeError("The selected vault must use RBAC in the configured tenant/subscription.")
    state["vaultSelection"] = {
        "id": resource["id"], "url": resource["properties"]["vaultUri"],
        "secretPrefix": state["prefix"],
        "external": resource.get("tags", {}).get("environmentId") != state["environmentId"],
    }
    save(state)
    site_path = (f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
                 f"/providers/Microsoft.Web/sites/{state['azure']['webName']}?api-version=2023-12-01")
    site = arm.call("GET", site_path)
    if site.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise RuntimeError("Refusing to grant secret access to an unowned web application.")
    for suffix in ("web-client-secret", "token-encryption-key"):
        scope = resource["id"] + "/secrets/" + state["prefix"] + "-" + suffix
        for principal, kind, role in (
            (site["identity"]["principalId"], "ServicePrincipal", "4633458b-17de-408a-b874-0445c86b69e6"),
            (state["operatorId"], "User", "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"),
        ):
            assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}|{principal}|{role}"))
            arm.call("PUT", scope + "/providers/Microsoft.Authorization/roleAssignments/"
                     + assignment + "?api-version=2022-04-01", {"properties": {
                         "principalId": principal, "principalType": kind,
                         "roleDefinitionId": f"/subscriptions/{state['subscriptionId']}"
                         + "/providers/Microsoft.Authorization/roleDefinitions/" + role,
                     }})


def ensure_web_identity(state, vault_name=None):
    azure = state["azure"]
    credential, arm, graph = clients(state)
    if vault_name or "vaultSelection" not in state:
        select_vault(state, vault_name or state["prefix"] + "-vault", arm)
    selection = state["vaultSelection"]
    vault = Rest(credential, selection["url"], "https://vault.azure.net/.default")
    secret_tags = {"environmentId": state["environmentId"], "solution": "fnol-delegated-v2"}
    key_path = "/secrets/" + selection["secretPrefix"] + "-token-encryption-key?api-version=7.4"
    key = vault.call("GET", key_path, missing_ok=True, sensitive=True)
    if key is None:
        vault.call("PUT", key_path, {
            "value": Fernet.generate_key().decode(), "tags": secret_tags,
        }, sensitive=True)
    elif key.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise RuntimeError("Refusing to use or overwrite an existing secret from another environment.")
    app_info = state.get("webIdentity")
    if not app_info:
        resource = graph.call(
            "GET", "/v1.0/servicePrincipals?$filter=" + quote(
                "servicePrincipalNames/any(s:s eq 'https://ai.azure.com')", safe="",
            ) + "&$select=appId,oauth2PermissionScopes",
        )["value"]
        if len(resource) != 1:
            raise RuntimeError("The tenant's Foundry API service principal is not uniquely available.")
        scopes = [scope for scope in resource[0]["oauth2PermissionScopes"]
                  if scope["value"] == "user_impersonation" and scope["isEnabled"]]
        if len(scopes) != 1:
            raise RuntimeError("The Foundry delegated user_impersonation scope is unavailable.")
        name = state["prefix"] + "-private-web"
        existing = graph.call(
            "GET", "/v1.0/applications?$filter=" + quote(f"displayName eq '{name}'", safe=""),
        )["value"]
        if existing:
            if len(existing) != 1 or state["environmentId"] not in existing[0].get("tags", []):
                raise RuntimeError("Refusing to adopt an existing Entra application.")
            application = existing[0]
        else:
            application = graph.call("POST", "/v1.0/applications", {
                "displayName": name, "signInAudience": "AzureADMyOrg",
                "tags": ["fnol-delegated-v2", state["environmentId"]],
                "web": {
                    "redirectUris": [azure["webUrl"] + "/auth/callback"],
                    "implicitGrantSettings": {"enableAccessTokenIssuance": False, "enableIdTokenIssuance": False},
                },
                "requiredResourceAccess": [{
                    "resourceAppId": resource[0]["appId"],
                    "resourceAccess": [{"id": scopes[0]["id"], "type": "Scope"}],
                }],
            })
        app_info = {"objectId": application["id"], "clientId": application["appId"]}
        state["webIdentity"] = app_info
        save(state)
    principals = graph.call(
        "GET", "/v1.0/servicePrincipals?$filter=" + quote(f"appId eq '{app_info['clientId']}'", safe=""),
    )["value"]
    if len(principals) > 1:
        raise RuntimeError("Ambiguous application service principal.")
    if not principals:
        principals = [graph.call("POST", "/v1.0/servicePrincipals", {"appId": app_info["clientId"]})]
    app_info["principalId"] = principals[0]["id"]
    secret_path = "/secrets/" + selection["secretPrefix"] + "-web-client-secret?api-version=7.4"
    existing_secret = vault.call("GET", secret_path, missing_ok=True, sensitive=True)
    if existing_secret is not None and (
        existing_secret.get("tags", {}).get("environmentId") != state["environmentId"]
        or existing_secret.get("tags", {}).get("clientId") != app_info["clientId"]
    ):
        raise RuntimeError("The existing client secret does not belong to this v2 application.")
    if existing_secret is None:
        if app_info.get("pendingSecretId"):
            graph.call(
                "POST", f"/v1.0/applications/{app_info['objectId']}/removePassword",
                {"keyId": app_info["pendingSecretId"]}, sensitive=True,
            )
        secret = graph.call(
            "POST", f"/v1.0/applications/{app_info['objectId']}/addPassword",
            {"passwordCredential": {
                "displayName": "v2-web-90-days",
                "endDateTime": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
            }}, sensitive=True,
        )
        app_info["pendingSecretId"] = secret["keyId"]
        save(state)
        vault.call("PUT", secret_path, {
            "value": secret["secretText"], "tags": {**secret_tags, "clientId": app_info["clientId"]},
        }, sensitive=True)
        app_info["secretExpiresAt"] = secret["endDateTime"]
        del app_info["pendingSecretId"]
    save(state)
    configure_app(state, arm)
    print("New single-tenant web identity configured; secret stored only in Key Vault.")


def configure_app(state, arm=None):
    if arm is None:
        _, arm, _ = clients(state)
    azure = state["azure"]
    base = (f"/subscriptions/{state['subscriptionId']}/resourceGroups/{state['resourceGroup']}"
            f"/providers/Microsoft.Web/sites/{azure['webName']}/config/appsettings")
    existing = arm.call("POST", base + "/list?api-version=2023-12-01", sensitive=True)["properties"]
    existing.pop("FNOL_V2_CLIENT_SECRET", None)
    existing.pop("FNOL_V2_ENCRYPTION_KEY", None)
    existing.update({
        "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
        "FNOL_V2_TENANT_ID": state["tenantId"],
        "FNOL_V2_CLIENT_ID": state["webIdentity"]["clientId"],
        "FNOL_V2_VAULT_URL": state["vaultSelection"]["url"].rstrip("/"),
        "FNOL_V2_SECRET_PREFIX": state["vaultSelection"]["secretPrefix"],
        "FNOL_V2_PUBLIC_URL": azure["webUrl"],
        "FNOL_V2_STORAGE_URL": azure["storageUrl"],
        "FNOL_V2_PROJECT_ENDPOINT": azure["projectEndpoint"],
        "FNOL_V2_AGENT_NAME": state.get("agent", {}).get("name", "fnol-v2-interactive"),
        "FNOL_V2_AGENT_VERSION": state.get("agent", {}).get("version", "not-configured"),
        "FNOL_V2_INTEGRATIONS_READY": "false",
        "FNOL_V2_TEAMS_ENABLED": str(state.get("teams", {}).get("enabled", False)).lower(),
    })
    mail = state.get("emailIntake", {})
    if mail.get("backendConfigured"):
        existing["FNOL_V2_EMAIL_OPERATOR_ID"] = state["operatorId"]
        existing["FNOL_V2_EMAIL_WORKFLOW_ID"] = mail["workflowId"]
        existing["FNOL_V2_EMAIL_CONFIG"] = json.dumps({
            "principal_id": mail["workflowPrincipalId"], "client_id": mail["workflowClientId"],
            "mailbox": mail["mailbox"], "folder_id": mail["folderId"],
            "subject_tag": mail["subjectTag"], "assigned_users": mail["assignedUsers"],
            "agent_name": state["emailTriageAgent"]["name"],
            "agent_version": state["emailTriageAgent"]["version"],
            "catalog_id": state["teams"]["catalogId"],
        })
    arm.call("PUT", base + "?api-version=2023-12-01", {"properties": existing}, sensitive=True)


def package_app(archive_path):
    included = RUNTIME_MODULES
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "v2" / "requirements.txt", "requirements.txt")
        paths = [ROOT / "v2" / name for name in sorted(included)]
        for folder, pattern in (("templates", "*.html"), ("static", "*.js"), ("static", "*.css")):
            paths.extend((ROOT / "v2" / folder).glob(pattern))
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())


def publish_app(state):
    if "webIdentity" not in state:
        raise RuntimeError("Configure the web identity before publishing.")
    archive_path = STATE.parent / "application.zip"
    package_app(archive_path)
    az("webapp", "deploy", "--resource-group", state["resourceGroup"],
       "--name", state["azure"]["webName"], "--src-path", str(archive_path),
       "--type", "zip", "--timeout", "1200000", "--track-status", "false")
    verify_published_app(state)


def verify_published_app(state):
    deployments = az(
        "webapp", "log", "deployment", "list",
        "--resource-group", state["resourceGroup"], "--name", state["azure"]["webName"],
    )
    if not deployments:
        raise RuntimeError("No application deployment exists.")
    latest = deployments[0]
    if latest.get("status") != 4 or latest.get("complete") is not True or latest.get("active") is not True:
        raise RuntimeError("The latest application deployment is not completed, successful and active.")
    origin = state["azure"]["webUrl"]
    for attempt in range(12):
        response = requests.get(origin + "/healthz", timeout=(15, 60), allow_redirects=False)
        if (response.status_code == 200 and response.json().get("status") == "running"
                and response.json().get("build_sha256") == runtime_digest()):
            break
        if attempt < 11:
            print("Waiting for the completed deployment's exact runtime build to become healthy.")
            time.sleep(5)
    if response.status_code != 200 or response.json().get("status") != "running":
        raise RuntimeError(f"The deployed app is not healthy: HTTP {response.status_code}.")
    if response.json().get("build_sha256") != runtime_digest():
        raise RuntimeError("The live runtime does not match the current packaged application source.")
    anonymous = requests.get(origin + "/api/cases", timeout=(15, 60), allow_redirects=False)
    if anonymous.status_code != 401:
        raise RuntimeError("Anonymous case-access rejection was not verified.")
    state.setdefault("application", {}).update({
        "deployed": True, "deploymentId": latest["id"], "anonymousCaseAccessRejected": True,
        "buildSha256": response.json()["build_sha256"],
    })
    save(state)
    print("Private application deployed; anonymous case access is rejected.")
