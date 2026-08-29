"""
Thread/run lifecycle helper for calling the Foundry orchestrator agent from
the Logic App / Azure Function trigger layer.

All calls target the PUBLIC Foundry Agent Service REST endpoint
(FOUNDRY_PROJECT_ENDPOINT, e.g. https://<project>.services.ai.azure.com) with
Entra ID (AzureCliCredential locally / ManagedIdentityCredential or a service
principal in the Logic App/Function) - no VNet, Private Endpoint, or other
network isolation component is used or required.

Exposes one function, run_case_through_orchestrator(...), that the Logic
App's HTTP actions (or the Function alternative) call once per incoming
email:

  1. Creates a NEW Foundry thread for this case (never reuses one).
  2. Posts the email content as the user message.
  3. Starts a run with the orchestrator agent.
  4. Polls until the run completes.
  5. Returns (thread_id, response_text) for the caller to post to Teams and
     persist in CaseThreadMap (see ../shared/case_thread_store_schema.sql and
     ../docs/teams-concurrency-design.md).

For FOLLOW-UP turns on an existing case, pass an existing thread_id in and
this skips thread creation, preserving full prior context.
"""
import os
import sys
import time

from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
POLL_INTERVAL_SECONDS = 2
MAX_POLL_SECONDS = 120


def _get_orchestrator_agent_id() -> str:
    id_file = os.path.join(os.path.dirname(__file__), "orchestrator_agent_id.txt")
    with open(id_file) as f:
        return f.read().strip()


def run_case_through_orchestrator(
    message_text: str,
    thread_id: str | None = None,
) -> tuple[str, str]:
    """Runs a single message through the orchestrator agent.

    If thread_id is None, a new thread is created (new case). If provided,
    the message is appended to that existing thread (follow-up on an
    existing case), preserving full context per the concurrency design.

    Returns (thread_id, response_text).
    """
    credential = DefaultAzureCredential()
    agents_client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    agent_id = _get_orchestrator_agent_id()

    if thread_id is None:
        thread = agents_client.threads.create()
        thread_id = thread.id

    agents_client.messages.create(thread_id=thread_id, role="user", content=message_text)

    run = agents_client.runs.create(thread_id=thread_id, agent_id=agent_id)

    elapsed = 0
    while run.status in ("queued", "in_progress", "requires_action"):
        if elapsed >= MAX_POLL_SECONDS:
            raise TimeoutError(
                f"Run {run.id} on thread {thread_id} did not complete within {MAX_POLL_SECONDS}s"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        run = agents_client.runs.get(thread_id=thread_id, run_id=run.id)

    if run.status != "completed":
        raise RuntimeError(f"Run {run.id} on thread {thread_id} ended with status={run.status}")

    messages = agents_client.messages.list(thread_id=thread_id, order="desc", limit=1)
    latest = next(iter(messages), None)
    response_text = ""
    if latest and latest.content:
        for part in latest.content:
            if getattr(part, "type", None) == "text":
                response_text += part.text.value

    return thread_id, response_text


if __name__ == "__main__":
    # Local smoke test — requires `az login` and FOUNDRY_PROJECT_ENDPOINT set.
    sample_email = (
        "Subject: Car accident on I-40\n\n"
        "Policyholder POL-00005 reports a collision with injuries and a possible "
        "hit-and-run driver. No police report has been filed yet."
    )
    tid, response = run_case_through_orchestrator(sample_email)
    print("Thread:", tid)
    print("Response:\n", response)
