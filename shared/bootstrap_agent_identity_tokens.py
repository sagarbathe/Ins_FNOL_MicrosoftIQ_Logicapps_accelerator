"""
One-time interactive bootstrap of the Agent Identity's MSAL token cache.

Signs in ONCE (device-code flow, interactive) as the Agent Identity user
(AGENT_IDENTITY_UPN / AGENT_IDENTITY_TENANT_ID in .env) for each of the three
resource scope sets this solution needs:
  1. Microsoft Graph   (Mail.Read, Sites.Read.All, Chat.Read, ChannelMessage.Send)
  2. Power BI / Fabric (.default)
  3. Azure SQL Database (.default)

All three end up in the SAME on-disk token cache (AGENT_IDENTITY_TOKEN_CACHE_PATH),
because a single MSAL PublicClientApplication + SerializableTokenCache can
hold refresh tokens for multiple resources simultaneously - only the initial
acquisition needs to be done once per resource; agent_identity_auth.py's
get_agent_token() then refreshes silently from then on.

Usage:
    python shared/bootstrap_agent_identity_tokens.py

Run this after:
  - Creating the Agent Identity user and its public-client app registration
    (see docs/End_to_End_Deployment_Guide.docx, Section 6, Steps 1-2)
  - Populating .env with AGENT_IDENTITY_TENANT_ID, AGENT_IDENTITY_UPN,
    AGENT_IDENTITY_PUBLIC_CLIENT_ID, AGENT_IDENTITY_TOKEN_CACHE_PATH

A browser window is NOT required on the machine running this script - the
device-code flow prints a URL + short code; complete the sign-in on ANY
device, using the Agent Identity's own credentials (not your own).
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402  (loads .env via python-dotenv)

sys.path.insert(0, os.path.dirname(__file__))
from agent_identity_auth import FABRIC_SCOPE, GRAPH_SCOPE, _app, _load_cache, _save_cache, _cache_path  # noqa: E402

SQL_SCOPE = ["https://database.windows.net/.default"]

SCOPE_SETS = [
    ("Microsoft Graph", GRAPH_SCOPE),
    ("Power BI / Fabric", FABRIC_SCOPE),
    ("Azure SQL Database", SQL_SCOPE),
]


def main():
    cache = _load_cache()
    app = _app(cache)

    for label, scopes in SCOPE_SETS:
        print(f"\n=== Bootstrapping {label} scopes: {scopes} ===")
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to start device flow for {label}: {flow}")
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
        _save_cache(cache)
        if "access_token" not in result:
            raise RuntimeError(f"Device code sign-in failed for {label}: {result}")
        print(f"{label}: granted scopes ->", result.get("scope"))

    upn = result.get("id_token_claims", {}).get("preferred_username", "?")
    print(f"\nDone. Signed in and cached refresh tokens for: {upn}")
    print(f"Token cache written to: {_cache_path()}")

    # Also print the base64 form, needed as the AGENT_IDENTITY_TOKEN_CACHE_B64
    # app setting when deploying to Azure App Service (see deploy_webapp step).
    with open(_cache_path(), "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    b64_path = os.path.join(os.path.dirname(_cache_path()), "token_cache_b64.txt")
    with open(b64_path, "w", encoding="ascii") as f:
        f.write(b64)
    print(f"Base64-encoded cache (for the webapp's AGENT_IDENTITY_TOKEN_CACHE_B64 "
          f"app setting) written to: {b64_path}")


if __name__ == "__main__":
    main()
