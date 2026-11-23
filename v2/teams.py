"""Personal Teams app registration and packaging for the isolated v2 environment."""

import hashlib
import io
import json
import re
import struct
import uuid
import zipfile
import zlib
from urllib.parse import quote, urlencode, urlparse

import requests

from v2.auth import TEAMS_CLIENTS
from v2.deploy import STATE, save
from v2.links import TAB_ID, deep_link
from v2.provision import clients, configure_app

APP_VERSION = "1.0.0"


def resource_uri(origin, client_id):
    parsed = urlparse(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or parsed.username or parsed.port):
        raise ValueError("An explicit HTTPS app origin is required.")
    return f"api://{parsed.hostname}/{uuid.UUID(client_id)}"


def manifest(state):
    origin = state["azure"]["webUrl"].rstrip("/")
    identity = state["webIdentity"]
    resource = resource_uri(origin, identity["clientId"])
    app_id = str(uuid.UUID(state["teams"]["manifestId"]))
    return {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.19/MicrosoftTeams.schema.json",
        "manifestVersion": "1.19", "version": APP_VERSION, "id": app_id,
        "developer": {
            "name": "FNOL v2 internal pilot", "websiteUrl": origin,
            "privacyUrl": origin + "/privacy", "termsOfUseUrl": origin + "/terms",
        },
        "name": {"short": "FNOL Investigation", "full": "FNOL v2 private investigation pilot"},
        "description": {
            "short": "Private, user-authorized investigation of assigned synthetic FNOL cases.",
            "full": (
                "Investigate assigned synthetic FNOL cases privately inside Teams. "
                "Fabric IQ, Foundry IQ and Work IQ retain the requesting user's access boundaries. "
                "Answers are not posted to shared channels. Internal pilot; preview integrations."
            ),
        },
        "icons": {"outline": "outline.png", "color": "color.png"},
        "accentColor": "#174A67",
        "staticTabs": [{
            "entityId": TAB_ID, "name": "Investigate", "contentUrl": origin + "/teams",
            "scopes": ["personal"],
        }],
        "permissions": ["identity"],
        "validDomains": [urlparse(origin).hostname],
        "webApplicationInfo": {"id": identity["clientId"], "resource": resource},
    }


def icon(size, *, outline=False):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF,
        )

    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            # A simple original case-file symbol; outline assets use white on transparency.
            document = size // 4 <= x < 3 * size // 4 and size // 6 <= y < 5 * size // 6
            border = document and (
                x < size // 4 + max(2, size // 24) or x >= 3 * size // 4 - max(2, size // 24)
                or y < size // 6 + max(2, size // 24) or y >= 5 * size // 6 - max(2, size // 24)
                or (size // 3 <= x < 2 * size // 3 and y in range(size // 2, size // 2 + max(2, size // 24)))
            )
            pixels.extend((255, 255, 255, 255) if border else (
                (0, 0, 0, 0) if outline else (23, 74, 103, 255)
            ))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b""))


def package_teams(state, target=None):
    target = target or STATE.parent / "teams-app.zip"
    definition = manifest(state)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("manifest.json", json.dumps(definition, indent=2)),
            ("color.png", icon(192)), ("outline.png", icon(32, outline=True)),
        ):
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    return target


def package_content_sha256(package):
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        contents = sorted(
            (entry.filename, hashlib.sha256(archive.read(entry)).hexdigest())
            for entry in archive.infolist()
        )
    return hashlib.sha256(json.dumps(contents).encode()).hexdigest()


def configure_teams(state):
    _, arm, graph = clients(state)
    identity = state["webIdentity"]
    path = "/v1.0/applications/" + identity["objectId"]
    application = graph.call("GET", path)
    if (state["environmentId"] not in application.get("tags", [])
            or application["appId"] != identity["clientId"]
            or application.get("signInAudience") != "AzureADMyOrg"):
        raise ValueError("Teams SSO may modify only the owned single-tenant v2 application.")
    uri = resource_uri(state["azure"]["webUrl"], identity["clientId"])
    identifiers = application.get("identifierUris", [])
    if identifiers and identifiers != [uri]:
        raise ValueError("Existing API identifier differs; refusing to replace it.")
    api = application.get("api") or {}
    scopes = api.get("oauth2PermissionScopes", [])
    existing = [scope for scope in scopes if scope["value"] == "access_as_user"]
    if len(existing) > 1:
        raise ValueError("The Teams delegated scope is ambiguous.")
    scope_id = existing[0]["id"] if existing else str(uuid.uuid5(
        uuid.UUID(state["environmentId"]), "teams-access-as-user",
    ))
    if existing and (not existing[0].get("isEnabled") or existing[0].get("type") != "User"):
        raise ValueError("The existing Teams scope is disabled or has a different consent policy.")
    if not existing:
        scopes.append({
            "id": scope_id, "value": "access_as_user", "type": "User", "isEnabled": True,
            "adminConsentDisplayName": "Access FNOL v2 as the signed-in user",
            "adminConsentDescription": "Access assigned pilot cases using the signed-in user's permissions.",
            "userConsentDisplayName": "Access your FNOL v2 pilot cases",
            "userConsentDescription": "Investigate assigned synthetic cases using your permissions.",
        })
    authorized = api.get("preAuthorizedApplications", [])
    for client_id in TEAMS_CLIENTS:
        entries = [item for item in authorized if item["appId"] == client_id]
        if len(entries) > 1:
            raise ValueError("Ambiguous Teams client preauthorization.")
        if entries:
            entries[0]["delegatedPermissionIds"] = sorted(set(
                entries[0]["delegatedPermissionIds"] + [scope_id],
            ))
        else:
            authorized.append({"appId": client_id, "delegatedPermissionIds": [scope_id]})
    desired = {
        "identifierUris": [uri],
        "api": {
            "requestedAccessTokenVersion": 2, "oauth2PermissionScopes": scopes,
        },
    }
    # Graph requires the exposed scope to exist before another client can reference it.
    graph.call("PATCH", path, desired)
    actual = graph.call("GET", path)
    if (actual.get("identifierUris") != [uri]
            or actual["api"].get("requestedAccessTokenVersion") != 2
            or actual["api"].get("oauth2PermissionScopes") != scopes):
        raise RuntimeError("Teams SSO registration readback differs; app hosting was not enabled.")
    graph.call("PATCH", path, {"api": {"preAuthorizedApplications": authorized}})
    actual = graph.call("GET", path)
    def permissions(entries):
        return sorted(
            (entry["appId"], tuple(sorted(entry["delegatedPermissionIds"]))) for entry in entries
        )

    if permissions(actual["api"].get("preAuthorizedApplications", [])) != permissions(authorized):
        raise RuntimeError("Teams client preauthorization readback differs; app hosting was not enabled.")
    teams = state.setdefault("teams", {})
    teams.update({
        "manifestId": str(uuid.uuid5(uuid.UUID(state["environmentId"]), "teams-personal-app")),
        "resourceUri": uri, "scopeId": scope_id, "identityConfigured": True,
        "enabled": True, "endToEndVerified": False,
    })
    package = package_teams(state)
    teams["packageSha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    teams["packageVersion"] = APP_VERSION
    save(state)
    configure_app(state, arm)
    print("Teams SSO registered and package built at .secrets\\v2\\teams-app.zip. "
          "App installation and real Teams user acceptance are separate gates.")


def publish_teams(state):
    if not state.get("teams", {}).get("identityConfigured"):
        raise ValueError("Configure Teams SSO before submitting the personal app.")
    credential, _, graph = clients(state)
    teams = state["teams"]
    package = package_teams(state).read_bytes()
    digest = hashlib.sha256(package).hexdigest()
    content_digest = package_content_sha256(package)
    apps = graph.call("GET", "/v1.0/appCatalogs/teamsApps?$filter="
                      + quote("externalId eq '" + teams["manifestId"] + "'", safe=""))["value"]
    if len(apps) > 1:
        raise ValueError("The Teams catalog app is not unique.")
    if apps:
        if apps[0]["id"] != teams.get("catalogId"):
            raise ValueError("Refusing to adopt an untracked Teams catalog app.")
        if (teams.get("publishedSha256") != digest
                and teams.get("publishedContentSha256") != content_digest):
            raise ValueError("Package changed; increment its version and submit a reviewed catalog update.")
        print("The identical personal app is already submitted to the Teams catalog.")
        return
    response = requests.post(
        "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps?requiresReview=true",
        headers={
            "Authorization": "Bearer " + credential.get_token("https://graph.microsoft.com/.default").token,
            "Content-Type": "application/zip",
        }, data=package, timeout=(15, 120), allow_redirects=False,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Teams catalog submission failed: HTTP {response.status_code}. "
                           "Use the generated package in Teams admin center; no tenant policy was changed.")
    teams.update(
        catalogId=response.json()["id"], publishedSha256=digest,
        publishedContentSha256=content_digest, reviewRequired=True,
    )
    save(state)
    print("Personal Teams app submitted for tenant review; this is not installation or user acceptance.")
