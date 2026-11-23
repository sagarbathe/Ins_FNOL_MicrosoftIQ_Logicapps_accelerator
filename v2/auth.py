import time
import uuid
from functools import lru_cache

import jwt
import msal
from azure.core.credentials import AccessToken

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
TEAMS_CLIENTS = (
    "1fec8e78-bce4-4aaf-ab1b-5451cc387264",
    "5e3ce6c0-2b1f-4285-8d4b-75ee78787346",
)


class InteractionRequired(RuntimeError):
    pass


@lru_cache(maxsize=4)
def signing_keys(tenant_id):
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        lifespan=3600, timeout=10,
    )


def teams_session(settings, token):
    """Validate our API token, never Teams context or a downstream API token."""
    if not settings.teams_enabled:
        raise InteractionRequired("Teams single sign-on is not enabled.")
    try:
        key = signing_keys(settings.tenant_id).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.client_id,
            issuer=f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0",
            options={"require": ["exp", "nbf", "iat", "aud", "iss", "tid", "oid", "scp", "azp"]},
        )
        if (claims["tid"] != settings.tenant_id or claims.get("ver") != "2.0"
                or claims["azp"] not in TEAMS_CLIENTS
                or "access_as_user" not in claims["scp"].split()):
            raise ValueError("Invalid Teams user token claims.")
        user_id = str(uuid.UUID(claims["oid"]))
    except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, TypeError, AttributeError) as exc:
        raise InteractionRequired("Teams identity validation failed. Retry Teams sign-in.") from exc
    return {
        "tenant_id": settings.tenant_id, "user_id": user_id,
        "expires_at": claims["exp"], "teams_assertion": token,
    }


def intake_identity(settings, token):
    if not settings.email_principal_id or not settings.email_client_id:
        raise InteractionRequired("Email intake is not configured.")
    try:
        key = signing_keys(settings.tenant_id).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.client_id,
            issuer=f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0",
            options={"require": ["exp", "nbf", "iat", "aud", "iss", "tid", "oid", "azp"]},
        )
        if (claims["tid"] != settings.tenant_id or claims.get("ver") != "2.0"
                or claims["oid"] != settings.email_principal_id
                or claims["azp"] != settings.email_client_id or "scp" in claims):
            raise ValueError("Not the isolated workflow application identity.")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, TypeError, AttributeError) as exc:
        raise InteractionRequired("Email intake application identity validation failed.") from exc


def client(settings, session):
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    application = msal.ConfidentialClientApplication(
        settings.client_id,
        authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
        client_credential=settings.client_secret,
        token_cache=cache,
    )
    return application, cache


class DelegatedCredential:
    """Only the current MSAL user can authorize calls; no infrastructure credential fallback."""

    def __init__(self, settings, session):
        self.settings = settings
        self.session = session

    def get_token(self, *scopes, **kwargs):
        if set(scopes) != {FOUNDRY_SCOPE}:
            raise ValueError("This credential may acquire only the Foundry user audience.")
        if self.session.get("tenant_id") != self.settings.tenant_id or not self.session.get("user_id"):
            raise InteractionRequired("Sign in to the configured tenant.")
        application, cache = client(self.settings, self.session)
        if self.session.get("teams_assertion"):
            result = application.acquire_token_on_behalf_of(
                self.session["teams_assertion"], scopes=[FOUNDRY_SCOPE],
            )
            if not result or "access_token" not in result:
                raise InteractionRequired(
                    "Foundry delegated authorization requires consent or additional sign-in. "
                    "Ask the administrator to verify the app's delegated Foundry consent; "
                    "no fallback identity was used."
                )
            return AccessToken(result["access_token"], int(time.time()) + int(result["expires_in"]))
        accounts = [
            account for account in application.get_accounts()
            if account.get("local_account_id") == self.session["user_id"]
            and account.get("realm") == self.settings.tenant_id
        ]
        if len(accounts) != 1:
            raise InteractionRequired("The signed-in account is not uniquely available.")
        result = application.acquire_token_silent([FOUNDRY_SCOPE], account=accounts[0])
        self.session["token_cache"] = cache.serialize()
        if not result or "access_token" not in result:
            raise InteractionRequired("User sign-in or consent is required.")
        return AccessToken(result["access_token"], int(time.time()) + int(result["expires_in"]))
