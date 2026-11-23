"""Administrative case seeding inside the private-connected application container."""

import base64
import hashlib
import inspect
import json
import time
import uuid

import requests

from v2.deploy import az, save
from v2.provision import clients


def _create_encrypted_cases(storage_url, payload):
    import base64
    import json
    import os
    from urllib.error import HTTPError
    from urllib.parse import quote, urlencode
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    http = build_opener(NoRedirect())
    endpoint = os.environ["IDENTITY_ENDPOINT"] + "?" + urlencode({
        "resource": "https://storage.azure.com/", "api-version": "2019-08-01",
    })
    with http.open(Request(endpoint, headers={
        "X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"],
    }), timeout=30) as response:
        token = json.load(response)["access_token"]
    headers = {"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03"}
    result = {}
    for key, encoded in payload.items():
        url = storage_url.rstrip("/") + "/private-state/" + quote(key, safe="/")
        try:
            with http.open(Request(url, headers=headers), timeout=30) as response:
                content = response.read()
        except HTTPError as exc:
            if exc.code != 404:
                raise
            try:
                with http.open(Request(url, method="PUT", data=base64.b64decode(encoded), headers={
                    **headers, "x-ms-blob-type": "BlockBlob", "If-None-Match": "*",
                }), timeout=30):
                    pass
            except HTTPError as conflict:
                if conflict.code != 412:
                    raise
            with http.open(Request(url, headers=headers), timeout=30) as response:
                content = response.read()
        result[key] = base64.b64encode(content).decode("ascii")
    return result


def seed_inside_app(state, cases, cipher):
    if not state.get("privateNetwork"):
        raise ValueError("Deploy private application-state networking before seeding cases.")
    if state["azure"]["storageUrl"].rstrip("/") != (
        "https://" + state["prefix"] + "state.blob.core.windows.net"
    ):
        raise ValueError("Case bootstrap must target the owned v2 state account.")
    credential, _, _ = clients(state)
    site = az("webapp", "show", "--resource-group", state["resourceGroup"],
              "--name", state["azure"]["webName"])
    if site.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("Refusing to execute case bootstrap in an unowned web application.")
    origin = "https://" + state["azure"]["webName"] + ".scm.azurewebsites.net"
    headers = {"Authorization": "Bearer " + credential.get_token(
        "https://management.azure.com/.default",
    ).token}
    expected = {"cases/" + case["id"]: case for case in cases}
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    operation = state.get("caseBootstrapOperation")
    if operation and operation["status"] != "verified" and operation["caseDigest"] != digest:
        raise ValueError("An unfinished bootstrap has different assignments; resolve it first.")
    if not operation or operation["status"] == "verified":
        name = "v2-case-bootstrap-" + uuid.uuid4().hex
        operation = {"name": name, "caseDigest": digest, "status": "prepared"}
        state["caseBootstrapOperation"] = operation
        payload = {
            key: base64.b64encode(cipher.encrypt(json.dumps(value).encode())).decode("ascii")
            for key, value in expected.items()
        }
        source = inspect.getsource(_create_encrypted_cases) + (
            "\nfrom pathlib import Path\nimport json\n"
            "try:\n"
            f"    data = _create_encrypted_cases({state['azure']['storageUrl']!r}, {payload!r})\n"
            "    result = {'status': 'completed', 'data': data}\n"
            "except Exception as exc:\n"
            "    result = {'status': 'failed', 'errorType': type(exc).__name__, "
            "'httpStatus': getattr(exc, 'code', None)}\n"
            f"Path('/home/{name}.json').write_text(json.dumps(result))\n"
        )
        response = requests.put(
            origin + "/api/vfs/" + name + ".py", data=source.encode(),
            headers={**headers, "If-None-Match": "*"}, timeout=(15, 60), allow_redirects=False,
        )
        response.raise_for_status()
        save(state)
    name = operation["name"]
    if operation["status"] == "prepared":
        # Persist before submission: an interrupted CLI request must not be blindly repeated.
        operation["status"] = "submitted"
        save(state)
        az("webapp", "exec", "--resource-group", state["resourceGroup"],
           "--name", state["azure"]["webName"], "--mode", "execute",
           "--command", "python /home/" + name + ".py")
    for _ in range(60):
        response = requests.get(
            origin + "/api/vfs/" + name + ".json", headers=headers,
            timeout=(15, 60), allow_redirects=False,
        )
        if response.status_code == 404:
            time.sleep(3)
            continue
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "completed":
            raise RuntimeError(f"Private case bootstrap failed: {result.get('errorType')} "
                               f"(HTTP {result.get('httpStatus')}).")
        actual = {
            key: json.loads(cipher.decrypt(base64.b64decode(value)))
            for key, value in result["data"].items()
        }
        if actual != expected:
            raise ValueError("Existing case assignments differ; they were not overwritten.")
        operation["status"] = "verified"
        save(state)
        for suffix in (".py", ".json"):
            cleanup = requests.delete(
                origin + "/api/vfs/" + name + suffix, headers={**headers, "If-Match": "*"},
                timeout=(15, 60), allow_redirects=False,
            )
            cleanup.raise_for_status()
        return
    raise TimeoutError("Case bootstrap is not confirmed complete; rerun cases to inspect the same run.")
