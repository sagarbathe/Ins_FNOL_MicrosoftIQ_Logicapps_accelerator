"""Human-confirmed, user-delegated sending, separate from the read-only agent."""

import hashlib
import json
import re
import secrets
import time
import uuid

import requests
from azure.core.exceptions import ResourceExistsError

from v2 import auth
from v2.state import StateBusy, run_prefix

MAIL_SCOPE = "https://graph.microsoft.com/Mail.Send"
ADDRESS = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)


class MailSendError(RuntimeError):
    pass


def user_mail_token(settings, session):
    if session.get("tenant_id") != settings.tenant_id or not session.get("user_id"):
        raise auth.InteractionRequired("The signed-in user is required for escalation.")
    application, cache = auth.client(settings, session)
    if session.get("teams_assertion"):
        result = application.acquire_token_on_behalf_of(session["teams_assertion"], scopes=[MAIL_SCOPE])
    else:
        accounts = [account for account in application.get_accounts()
                    if account.get("local_account_id") == session["user_id"]
                    and account.get("realm") == settings.tenant_id]
        if len(accounts) != 1:
            raise auth.InteractionRequired("The signed-in mail account is not uniquely available.")
        result = application.acquire_token_silent([MAIL_SCOPE], account=accounts[0])
        session["token_cache"] = cache.serialize()
    if not result or "access_token" not in result:
        raise auth.InteractionRequired(
            "Your delegated Mail.Send consent or sign-in is required. No shared mailbox fallback was used."
        )
    return result["access_token"]


def draft_key(owner, draft_id):
    if not isinstance(draft_id, str) or not re.fullmatch(r"[a-f0-9]{64}", draft_id):
        raise ValueError("Invalid escalation draft identifier.")
    return "escalations/" + hashlib.sha256(json.dumps(owner).encode()).hexdigest() + "/" + draft_id


def preview(store, owner, body):
    if not isinstance(body, dict) or set(body) != {"recipient", "subject", "body"}:
        raise ValueError("Enter a literal recipient, subject and message for preview.")
    if (not isinstance(body["recipient"], str) or len(body["recipient"]) > 254
            or not ADDRESS.fullmatch(body["recipient"])):
        raise ValueError("Enter one literal email address supplied by you; addresses are never inferred.")
    if (not isinstance(body["subject"], str) or not 1 <= len(body["subject"].strip()) <= 200
            or any(character in body["subject"] for character in "\r\n\x00")
            or "[FNOL-V2]" in body["subject"].upper()):
        raise ValueError("Use a bounded, single-line subject without the intake routing tag.")
    if (not isinstance(body["body"], str) or not 1 <= len(body["body"].strip()) <= 10000
            or "\x00" in body["body"]):
        raise ValueError("Enter a message of 1-10000 characters.")
    payload = {
        "recipient": body["recipient"], "subject": "[FNOL v2 escalation] " + body["subject"].strip(),
        "body": body["body"],
    }
    draft_id = hashlib.sha256(json.dumps([owner, payload], sort_keys=True).encode()).hexdigest()
    key = draft_key(owner, draft_id)
    draft = {
        "id": draft_id, "owner": owner, **payload, "phase": "preview",
        "confirmation_token": secrets.token_urlsafe(32), "expires_at": int(time.time()) + 600,
    }
    try:
        store.create(key, draft)
    except ResourceExistsError:
        with store.edit(key) as saved:
            if saved["owner"] != owner:
                raise ValueError("Escalation ownership does not match.")
            if saved["phase"] in ("preview", "rejected"):
                saved.update(draft)
            draft = dict(saved)
    return {key: value for key, value in draft.items() if key != "owner"}


def confirm(settings, store, session, owner, body):
    if (not isinstance(body, dict) or set(body) != {"draft_id", "confirmation_token", "confirmed"}
            or body["confirmed"] is not True):
        raise ValueError("Explicit confirmation of the immutable preview is required.")
    key = draft_key(owner, body["draft_id"])
    existing = store.read(key)
    if not existing or existing.get("owner") != owner:
        raise ValueError("The escalation draft is not owned by this user and case.")
    if existing["phase"] == "accepted":
        return {"status": "accepted", "duplicate": True, "recipient": existing["recipient"]}
    if existing["phase"] != "preview":
        raise StateBusy("The send outcome requires reconciliation. No automatic resend is allowed.")
    if (existing["expires_at"] <= time.time() or not isinstance(body["confirmation_token"], str)
            or not secrets.compare_digest(existing["confirmation_token"], body["confirmation_token"])):
        raise ValueError("The preview expired or its confirmation token does not match.")
    token = user_mail_token(settings, session)
    failure = None
    attempt_id = str(uuid.uuid4())
    with store.edit(key) as saved:
        if (saved["phase"] != "preview"
                or saved["confirmation_token"] != body["confirmation_token"]
                or saved["expires_at"] <= time.time()):
            raise StateBusy("The escalation preview changed or has already been consumed.")
        # Persist the reservation before the non-idempotent external request.
        saved["phase"] = "sending"
        saved["attempt_id"] = attempt_id
    run_key = run_prefix(owner) + attempt_id
    store.create(run_key, {
        "id": attempt_id, "owner": owner, "started_at": int(time.time()),
        "agent": {"name": "confirmed-escalation", "version": "1"}, "outcome": "sending",
        "tools": [{"type": "http", "server_label": "graph", "name": "me.sendMail", "status": "sending"}],
    })
    try:
        response = requests.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json={"message": {
                "subject": existing["subject"], "body": {"contentType": "Text", "content": existing["body"]},
                "toRecipients": [{"emailAddress": {"address": existing["recipient"]}}],
            }, "saveToSentItems": True}, timeout=(15, 60), allow_redirects=False,
        )
        phase = "accepted" if response.status_code == 202 else (
            "rejected" if 400 <= response.status_code < 500 else "uncertain"
        )
        status = response.status_code
        if phase != "accepted":
            failure = MailSendError(f"Microsoft Graph returned HTTP {status}; send status is {phase}.")
    except requests.RequestException as exc:
        phase, status = "uncertain", None
        failure = MailSendError("The send outcome is uncertain; reconcile Sent Items before any new send.")
        failure.__cause__ = exc
    with store.edit(key) as saved:
        saved.update(phase=phase, http_status=status, finished_at=int(time.time()))
    with store.edit(run_key) as record:
        record.update(finished_at=int(time.time()), outcome=phase)
        record["tools"][0]["status"] = phase
    if failure:
        raise failure
    return {"status": "accepted", "duplicate": False, "recipient": existing["recipient"]}
