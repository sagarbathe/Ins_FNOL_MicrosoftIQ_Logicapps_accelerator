"""Operator-confirmed recovery of one owned workflow, never investigation data access."""

import re
import time
import uuid
from urllib.parse import quote, urlparse

import requests
from azure.identity import ManagedIdentityCredential

from v2.email_processing import acknowledge_notification
from v2.runtime import GroundingError
from v2.state import StateBusy

RECOVERY_ACTIONS = [
    "Microsoft.Logic/workflows/read",
    "Microsoft.Logic/workflows/runs/read",
    "Microsoft.Logic/workflows/runs/actions/read",
    "Microsoft.Logic/workflows/triggers/histories/read",
    "Microsoft.Logic/workflows/triggers/histories/resubmit/action",
]


class RecoveryClient:
    def __init__(self, workflow_id):
        if not re.fullmatch(
            r"/subscriptions/[a-fA-F0-9-]{36}/resourceGroups/[A-Za-z0-9_.()-]+"
            r"/providers/Microsoft\.Logic/workflows/[A-Za-z0-9_-]+", workflow_id,
        ):
            raise ValueError("An explicit owned workflow resource ID is required.")
        self.workflow_id = workflow_id

    def call(self, method, suffix):
        if not suffix.startswith("/") or ".." in suffix:
            raise ValueError("Invalid owned workflow operation.")
        with ManagedIdentityCredential() as credential:
            token = credential.get_token("https://management.azure.com/.default").token
        response = requests.request(
            method, "https://management.azure.com" + self.workflow_id + suffix,
            headers={"Authorization": "Bearer " + token},
            timeout=(15, 120), allow_redirects=False,
        )
        if response.status_code not in (200, 202):
            raise GroundingError(f"Owned workflow recovery returned HTTP {response.status_code}.")
        return response.json() if response.content else {}

    def terminal_run(self, run_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
            raise ValueError("Invalid original workflow run identifier.")
        result = self.call("GET", "/runs/" + run_id + "?api-version=2019-05-01")
        if result["properties"]["status"] not in (
            "Succeeded", "Failed", "Cancelled", "TimedOut", "Aborted", "Faulted",
        ):
            raise StateBusy("The original workflow run is not terminal. Recovery was not submitted.")
        return result["properties"]

    def history_for_run(self, run_id):
        run = self.terminal_run(run_id)
        tracking = run.get("correlation", {}).get("clientTrackingId", "")
        if not isinstance(tracking, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tracking):
            raise GroundingError("The original run has no usable client tracking ID; no replay sent.")
        histories = self.call(
            "GET", "/triggers/New_pilot_email/histories?api-version=2019-05-01&$top=100&$filter="
            + quote("ClientTrackingId eq '" + tracking + "'", safe=""),
        )
        matches = [item for item in histories["value"]
                   if item["properties"].get("run", {}).get("name") == run_id]
        if len(matches) != 1:
            raise GroundingError("The exact original trigger history was not found uniquely; no replay sent.")
        name = matches[0]["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise GroundingError("The original trigger history identifier is invalid.")
        return name

    def resubmit(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise ValueError("Invalid resolved trigger history identifier.")
        self.call("POST", "/triggers/New_pilot_email/histories/" + name
                  + "/resubmit?api-version=2019-05-01")

    def delivered_message(self, run_id):
        action = self.call(
            "GET", "/runs/" + run_id + "/actions/Post_minimal_card?api-version=2019-05-01",
        )["properties"]
        if action["status"] != "Succeeded":
            raise GroundingError("Successful card delivery is not established; no acknowledgement recorded.")
        uri = action.get("outputsLink", {}).get("uri", "")
        parsed = urlparse(uri)
        if (parsed.scheme != "https" or not parsed.hostname
                or not parsed.hostname.endswith(".logic.azure.com")
                or parsed.username or parsed.port not in (None, 443) or parsed.fragment):
            raise GroundingError("The service returned an unrecognized action-output endpoint.")
        response = requests.get(uri, timeout=(15, 60), allow_redirects=False)
        if response.status_code != 200:
            raise GroundingError("The actual card delivery receipt is not available.")
        message_id = response.json().get("body", {}).get("id")
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256:
            raise GroundingError("The actual card response has no usable message identifier.")
        return message_id


def recover(store, case_id, operator_id, body, client):
    if (not isinstance(body, dict)
            or set(body) != {"action", "confirmed", "confirmed_no_delivery"}
            or body["confirmed"] is not True
            or type(body["confirmed_no_delivery"]) is not bool
            or body["action"] not in ("retry_screening", "retry_notification", "reconcile")):
        raise ValueError("Explicit operator confirmation of a recognized recovery action is required.")
    case = store.read("cases/" + case_id)
    if not case or "processing" not in case:
        raise ValueError("This case is not an email intake case.")
    processing = case["processing"]
    run_id = processing["run_id"]
    client.terminal_run(run_id)
    if body["action"] == "reconcile":
        message_id = client.delivered_message(run_id)
        acknowledge_notification(store, case_id, {
            "workflow_run_id": run_id, "notification_id": processing["notification_id"],
            "message_id": message_id,
        })
        return {"status": "delivery_reconciled", "resent": False}
    expected = ("received", "screening_failed") if body["action"] == "retry_screening" else (
        "notification_reserved",
    )
    if body["action"] == "retry_notification" and not body["confirmed_no_delivery"]:
        raise ValueError("First verify that no card was delivered; uncertain delivery is never auto-retried.")
    history_id = client.history_for_run(run_id)
    approved = "retry_approved" if body["action"] == "retry_screening" else "notification_retry_approved"
    recovery_id = str(uuid.uuid4())
    with store.edit("cases/" + case_id) as saved:
        current = saved["processing"]
        if current["run_id"] != run_id or current["phase"] not in expected:
            raise StateBusy("The processing attempt changed or a recovery is already pending.")
        current.update(phase=approved, recovery={
            "id": recovery_id, "operator_id": operator_id, "action": body["action"],
            "confirmed_no_delivery": body["confirmed_no_delivery"],
            "requested_at": int(time.time()), "status": "submitting",
        })
    try:
        client.resubmit(history_id)
    except Exception:
        with store.edit("cases/" + case_id) as saved:
            saved["processing"]["recovery"]["status"] = "unconfirmed"
        raise
    with store.edit("cases/" + case_id) as saved:
        saved["processing"]["recovery"]["status"] = "accepted"
    return {"status": "recovery_accepted", "recovery_id": recovery_id}
