"""Encrypted server-side state with leases shared across workers and app instances."""

import hashlib
import json
import secrets
import threading
import time
from contextlib import contextmanager

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from cryptography.fernet import Fernet


class StateBusy(RuntimeError):
    pass


class BlobState:
    def __init__(self, storage_url, encryption_key, credential):
        self.container = BlobServiceClient(
            account_url=storage_url, credential=credential,
        ).get_container_client("private-state")
        self.cipher = Fernet(encryption_key.encode("ascii"))

    def _encode(self, value):
        return self.cipher.encrypt(json.dumps(value).encode("utf-8"))

    def read(self, key):
        try:
            data = self.container.get_blob_client(key).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(self.cipher.decrypt(data))

    def create(self, key, value):
        self.container.get_blob_client(key).upload_blob(self._encode(value), overwrite=False)

    @contextmanager
    def edit(self, key):
        blob = self.container.get_blob_client(key)
        try:
            lease = blob.acquire_lease(lease_duration=60)
        except ResourceNotFoundError:
            yield None
            return
        except ResourceExistsError as exc:
            raise StateBusy("Another request is still using this session or conversation.") from exc
        stop = threading.Event()
        renewal_errors = []

        def renew():
            while not stop.wait(20):
                try:
                    lease.renew()
                except Exception as exc:
                    renewal_errors.append(exc)
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            value = json.loads(self.cipher.decrypt(blob.download_blob(lease=lease).readall()))
            yield value
            if renewal_errors:
                raise StateBusy("State lease renewal failed; the result was not saved.") from renewal_errors[0]
            blob.upload_blob(self._encode(value), overwrite=True, lease=lease)
        finally:
            stop.set()
            thread.join(timeout=10)
            lease.release()

    def cases(self, tenant_id, user_id):
        result = []
        for item in self.container.list_blobs(name_starts_with="cases/"):
            case = self.read(item.name)
            if (case and case.get("tenant_id") == tenant_id
                    and user_id in case.get("allowed_users", [])):
                result.append({"id": case["id"], "title": case["title"]})
        return result

    def runs(self, owner):
        result = []
        more = False
        for item in self.container.list_blobs(name_starts_with=run_prefix(owner)):
            if len(result) >= 1000:
                more = True
                break
            record = self.read(item.name)
            if not record or record.get("owner") != owner:
                raise ValueError("Run record ownership does not match its partition.")
            result.append(record)
        return sorted(result, key=lambda value: value["started_at"], reverse=True), more

    def new_session(self, value=None):
        session_id = secrets.token_urlsafe(32)
        session = dict(value or {})
        session["expires_at"] = int(time.time()) + 3600
        session["csrf"] = secrets.token_urlsafe(32)
        self.create("sessions/" + session_id, session)
        return session_id, session


def conversation_key(tenant_id, user_id, case_id, agent_name, agent_version):
    identity = [tenant_id, user_id, case_id, agent_name, agent_version]
    return "conversations/" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def run_prefix(owner):
    return "runs/" + hashlib.sha256(json.dumps(owner).encode()).hexdigest() + "/"
