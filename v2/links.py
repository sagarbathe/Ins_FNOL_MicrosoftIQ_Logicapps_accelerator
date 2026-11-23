import json
import re
import uuid
from urllib.parse import urlencode

TAB_ID = "investigation"


def deep_link(app_id, tenant_id, case_id=None):
    query = {"tenantId": str(uuid.UUID(tenant_id))}
    if case_id is not None:
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,63}", case_id):
            raise ValueError("Invalid case reference for a Teams deep link.")
        query["context"] = json.dumps({"subEntityId": case_id}, separators=(",", ":"))
    # Deliberately omit a standalone-web fallback.
    return f"https://teams.microsoft.com/l/entity/{uuid.UUID(app_id)}/{TAB_ID}?" + urlencode(query)
