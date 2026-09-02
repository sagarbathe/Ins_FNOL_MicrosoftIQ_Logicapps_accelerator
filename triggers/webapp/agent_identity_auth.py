"""
Delegated-auth helper for the dedicated "Agent Identity" (its own mailbox +
Teams membership) architecture option.

Instead of a service-principal / app-only (ClientSecretCredential) token,
this acquires tokens that represent a real, licensed Entra ID account
(e.g. svc-fnol-agent@<tenant>) that:
  - owns a real mailbox (Exchange Online license)
  - is a member of the Teams team/channel used for case alerts
  - has been granted direct workspace/project roles on the Fabric workspace
    and Foundry project (see docs/OBO_Bot_Teams_Design.docx, "Agent Identity"
    option)

Why this avoids On-Behalf-Of (OBO): the orchestrator does not hold a
different upstream caller's token and exchange it for another resource's
token. It silently reacquires tokens *directly as the agent identity itself*
for each downstream resource (Fabric, Graph, etc.) from a single MSAL token
cache. There is only ever one identity in the whole call chain.

One-time setup (per environment): run `python shared/agent_identity_auth.py
--bootstrap` once. This performs an interactive device-code sign-in as the
agent identity (a human must complete this once, including any required MFA
enrollment) and persists an encrypted-at-rest-by-the-OS token cache file at
AGENT_IDENTITY_TOKEN_CACHE_PATH. From then on, `get_agent_token(scope)` below
refreshes silently with no further interaction, for as long as the cached
refresh token remains valid (subject to the tenant's refresh token lifetime
policy).

The token cache file is NOT committed to source control (see .gitignore) -
treat it as a credential.
"""
import os
import sys

import msal

# Dedicated public-client app registration for this accelerator
# ("FNOL-AgentIdentity-GraphPublicClient", appId
# 414bf9dc-65ae-4338-8b38-6fc0a353a32a). The well-known Azure CLI client
# (04b07795-8ddb-461a-bbee-02f9e1bf7b46) works fine for Fabric/Power BI
# scopes but is a first-party Microsoft app that is NOT preauthorized for
# Graph Mail.Read / Sites.Read.All / Chat.Read / ChannelMessage.Send -
# requesting those scopes with it fails with AADSTS65002 ("Consent between
# first party application ... must be configured via preauthorization").
# This dedicated app has those four delegated Graph permissions configured
# (admin consent granted) plus is used for the Fabric scope too, so a
# single app/token cache covers both resources.
_PUBLIC_CLIENT_ID = os.environ.get(
    "AGENT_IDENTITY_PUBLIC_CLIENT_ID", "414bf9dc-65ae-4338-8b38-6fc0a353a32a"
)

FABRIC_SCOPE = ["https://api.fabric.microsoft.com/.default"]
GRAPH_SCOPE = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Sites.Read.All",
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/ChannelMessage.Send",
    "https://graph.microsoft.com/ChannelMessage.Read.All",
]


def _authority():
    tenant_id = os.environ["AGENT_IDENTITY_TENANT_ID"]
    return f"https://login.microsoftonline.com/{tenant_id}"


def _cache_path():
    return os.environ.get(
        "AGENT_IDENTITY_TOKEN_CACHE_PATH",
        os.path.join(os.path.dirname(__file__), "..", ".secrets", "agent_identity_token_cache.bin"),
    )


def _load_cache():
    """Load order:
    1. Local file at AGENT_IDENTITY_TOKEN_CACHE_PATH, if it already exists
       (normal case on a warm instance, or local dev).
    2. Otherwise, seed it once from AGENT_IDENTITY_TOKEN_CACHE_B64 (a
       base64-encoded copy of the cache, set as an App Service app setting)
       and write it out to the local path so this and subsequent calls in
       this instance's lifetime use the file directly. This exists because
       some Linux App Service / Oryx build pipelines discard loose files
       placed alongside the deployed code, so the app setting is the only
       reliably-persisted place to seed the cache from on a fresh instance.
    """
    cache = msal.SerializableTokenCache()
    path = _cache_path()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
        return cache

    seed_b64 = _read_seed_b64()
    if seed_b64:
        import base64

        serialized = base64.b64decode(seed_b64).decode("utf-8")
        cache.deserialize(serialized)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)
    return cache


def _read_seed_b64():
    """Reassembles the base64 token-cache seed from app settings.

    deploy_webapp.py splits the (often 30-40k+ char) base64 blob into
    AGENT_IDENTITY_TOKEN_CACHE_B64_0, _1, _2, ... plus a _COUNT setting to
    stay under App Service's ~50,000-char single-setting practical limit -
    reassemble those chunks in order here. Falls back to the legacy
    single unchunked AGENT_IDENTITY_TOKEN_CACHE_B64 setting for backward
    compatibility with any deployment that still uses it.
    """
    count_raw = os.environ.get("AGENT_IDENTITY_TOKEN_CACHE_B64_COUNT")
    if count_raw:
        count = int(count_raw)
        chunks = [os.environ.get(f"AGENT_IDENTITY_TOKEN_CACHE_B64_{i}", "") for i in range(count)]
        return "".join(chunks)
    return os.environ.get("AGENT_IDENTITY_TOKEN_CACHE_B64")


def _save_cache(cache):
    if not cache.has_state_changed:
        return
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cache.serialize())


def _app(cache):
    return msal.PublicClientApplication(
        client_id=_PUBLIC_CLIENT_ID,
        authority=_authority(),
        token_cache=cache,
    )


def get_agent_token(scope: list) -> str:
    """Silently returns a valid access token for `scope`, acquired directly
    as the agent identity (no OBO / confidential-client exchange). Raises if
    the one-time bootstrap sign-in (see module docstring) has not been done
    yet or the cached refresh token has expired and needs to be redone.
    """
    cache = _load_cache()
    app = _app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "No cached agent-identity account found. Run "
            "`python shared/agent_identity_auth.py --bootstrap` once to sign in "
            "interactively as the agent identity."
        )
    result = app.acquire_token_silent(scope, account=accounts[0])
    _save_cache(cache)
    if not result or "access_token" not in result:
        raise RuntimeError(
            f"Silent token acquisition failed for scope {scope}: "
            f"{result}. The cached refresh token may have expired - rerun "
            "the --bootstrap step."
        )
    return result["access_token"]


def _bootstrap():
    cache = _load_cache()
    app = _app(cache)
    flow = app.initiate_device_flow(scopes=FABRIC_SCOPE)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {flow}")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Device code sign-in failed: {result}")
    upn = result.get("id_token_claims", {}).get("preferred_username", "?")
    print(f"Signed in and cached refresh token for: {upn}")


if __name__ == "__main__":
    if "--bootstrap" in sys.argv:
        _bootstrap()
    else:
        print("Usage: python shared/agent_identity_auth.py --bootstrap")
