import os
import json
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from azure.identity import ManagedIdentityCredential


def vault_secrets(origin, secret_prefix):
    endpoint = urlparse(origin)
    if (endpoint.scheme != "https" or not endpoint.hostname
            or not endpoint.hostname.endswith(".vault.azure.net")
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment
            or endpoint.username or endpoint.port):
        raise ValueError("An explicit public-cloud Key Vault origin is required.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{5,47}", secret_prefix):
        raise ValueError("An explicit v2 secret namespace is required.")
    credential = ManagedIdentityCredential()
    try:
        token = credential.get_token("https://vault.azure.net/.default").token
        values = {}
        for field, suffix in (("client_secret", "web-client-secret"),
                              ("encryption_key", "token-encryption-key")):
            name = secret_prefix + "-" + suffix
            response = requests.get(
                origin.rstrip("/") + f"/secrets/{name}?api-version=7.4",
                headers={"Authorization": "Bearer " + token},
                timeout=(15, 60), allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Managed-identity Key Vault access failed: HTTP {response.status_code}.")
            value = response.json().get("value")
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"Key Vault secret {name} has no usable value.")
            values[field] = value
        return values
    finally:
        credential.close()


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    public_url: str
    storage_url: str
    encryption_key: str
    project_endpoint: str
    agent_name: str
    agent_version: str
    integrations_ready: bool = False
    teams_enabled: bool = False
    email_principal_id: str = ""
    email_client_id: str = ""
    email_mailbox: str = ""
    email_folder_id: str = ""
    email_subject_tag: str = ""
    email_assigned_users: tuple[str, ...] = ()
    email_agent_name: str = ""
    email_agent_version: str = ""
    teams_catalog_id: str = ""
    email_operator_id: str = ""
    email_workflow_id: str = ""

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.environ.get("FNOL_V2_" + name, "").strip()
            if not value or value.startswith("@Microsoft.KeyVault"):
                raise RuntimeError(f"FNOL_V2_{name} is missing or its Key Vault reference is unresolved.")
            return value

        values = {name.lower(): required(name) for name in (
            "TENANT_ID", "CLIENT_ID", "PUBLIC_URL", "STORAGE_URL",
            "PROJECT_ENDPOINT", "AGENT_NAME", "AGENT_VERSION",
        )}
        uuid.UUID(values["tenant_id"])
        uuid.UUID(values["client_id"])
        public = urlparse(values["public_url"])
        if (public.scheme != "https" or not public.hostname or public.path not in ("", "/")
                or public.query or public.fragment or public.username or public.port):
            raise ValueError("The public URL must be an HTTPS origin without a port.")
        values["public_url"] = values["public_url"].rstrip("/")
        project = urlparse(values["project_endpoint"])
        if (project.scheme != "https" or not project.hostname
                or not project.hostname.endswith(".services.ai.azure.com")
                or not project.path.startswith("/api/projects/")
                or project.query or project.fragment or project.username or project.port):
            raise ValueError("An explicit public-cloud Foundry project endpoint is required.")
        storage = urlparse(values["storage_url"])
        if (storage.scheme != "https" or not storage.hostname
                or not storage.hostname.endswith(".blob.core.windows.net")
                or storage.path not in ("", "/") or storage.query or storage.username or storage.port):
            raise ValueError("An explicit Azure Blob endpoint is required.")
        vault = os.environ.get("FNOL_V2_VAULT_URL", "").strip()
        if vault:
            values.update(vault_secrets(vault, required("SECRET_PREFIX")))
        else:
            values.update(client_secret=required("CLIENT_SECRET"), encryption_key=required("ENCRYPTION_KEY"))
        email = json.loads(os.environ.get("FNOL_V2_EMAIL_CONFIG", "{}"))
        if email:
            expected = {"principal_id", "client_id", "mailbox", "folder_id", "subject_tag",
                        "assigned_users", "agent_name", "agent_version", "catalog_id"}
            if (not isinstance(email, dict) or set(email) != expected
                    or any(not isinstance(value, str) or not value.strip()
                           for key, value in email.items() if key != "assigned_users")
                    or not isinstance(email["assigned_users"], list) or not email["assigned_users"]):
                raise ValueError("The complete isolated email configuration is required.")
            for name in ("principal_id", "client_id", "catalog_id"):
                uuid.UUID(email[name])
            email["assigned_users"] = tuple(str(uuid.UUID(user)) for user in email["assigned_users"])
            values.update({"email_" + name: value for name, value in email.items() if name != "catalog_id"})
            values["teams_catalog_id"] = email["catalog_id"]
        return cls(
            **values, integrations_ready=os.environ.get("FNOL_V2_INTEGRATIONS_READY") == "true",
            teams_enabled=os.environ.get("FNOL_V2_TEAMS_ENABLED") == "true",
            email_operator_id=os.environ.get("FNOL_V2_EMAIL_OPERATOR_ID", ""),
            email_workflow_id=os.environ.get("FNOL_V2_EMAIL_WORKFLOW_ID", ""),
        )
