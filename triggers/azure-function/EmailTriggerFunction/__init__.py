"""
Alternative (B.2) to the Logic App trigger (B.1): a fully code-first,
webhook-driven Azure Function that receives Microsoft Graph change
notifications for new mail and drives the same Foundry-run + Teams-post
pipeline as the Logic App, in Python.

This is a SKELETON, not a complete production implementation - it shows the
shape of the code-first path described in docs/design-options.md Option B.2.
The Logic App (triggers/logicapp/workflow.json) is the recommended default;
use this only if you want a connector-free, container-portable pipeline and
are prepared to own Graph subscription renewal (subscriptions for mail
change notifications expire after ~4230 minutes / ~3 days and must be
renewed on a timer - not implemented here, see the TODO below).

Deployed as a standard Azure Functions HTTP trigger over its public HTTPS
endpoint - no VNet integration or Private Endpoint is used or required.

Prerequisites (not implemented in this skeleton):
  1. A separate one-time/timer-triggered Function that calls
     POST https://graph.microsoft.com/v1.0/subscriptions
     with resource="/users/{fnolMailboxUserId}/messages",
     changeType="created", notificationUrl=<this function's public URL>,
     and renews it before expiration.
  2. Microsoft Graph's subscription validation handshake: on subscription
     creation, Graph calls this endpoint once with a `validationToken` query
     parameter that must be echoed back as plain text within 10 seconds -
     handled below.
"""
import json
import logging
import os
import sys

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from foundry.run_agent_thread import run_case_through_orchestrator  # noqa: E402
from shared.post_to_teams import post_new_case_alert  # noqa: E402

TEAM_ID = os.environ["TEAMS_TEAM_ID"]
CHANNEL_ID = os.environ["TEAMS_CHANNEL_ID"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    # Graph subscription validation handshake (fires once, on subscription
    # creation/renewal) - must echo the token back as text/plain.
    validation_token = req.params.get("validationToken")
    if validation_token:
        return func.HttpResponse(validation_token, status_code=200, mimetype="text/plain")

    body = req.get_json()
    for notification in body.get("value", []):
        resource_data = notification.get("resourceData", {})
        message_id = resource_data.get("id")
        if not message_id:
            continue

        # TODO: fetch the full message via Graph GET /users/{mailbox}/messages/{id}
        # using the same _get_graph_token() pattern as shared/post_to_teams.py,
        # then extract subject/body/internetMessageId for the calls below.
        # Left unimplemented in this skeleton - see triggers/logicapp/workflow.json
        # for the complete, working reference implementation of this same
        # end-to-end flow (recommended default path, per docs/design-options.md).
        email_subject = "<fetched subject>"
        email_body = "<fetched body>"

        message_text = f"Subject: {email_subject}\n\n{email_body}"
        thread_id, response_text = run_case_through_orchestrator(message_text)

        root_message_id = post_new_case_alert(TEAM_ID, CHANNEL_ID, response_text)

        # TODO: persist (CaseId, TeamId, ChannelId, root_message_id, thread_id)
        # into CaseThreadMap here, using the same idempotent-insert approach
        # described in docs/teams-concurrency-design.md step 5.
        logging.info(
            "Posted case alert: thread_id=%s root_message_id=%s", thread_id, root_message_id
        )

    return func.HttpResponse(json.dumps({"status": "accepted"}), status_code=202)
