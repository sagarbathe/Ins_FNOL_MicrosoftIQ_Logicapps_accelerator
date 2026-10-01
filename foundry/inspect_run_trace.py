r"""
Manual trace inspector for the orchestrator agent - a workaround for the
Foundry portal's Traces tab, which (as of this writing) only captures
conversations started in the portal's own Playground chat. Runs invoked
externally (Teams via la-fnol-teams-reply-poller, email intake via
la-fnol-email-intake, or any other direct Threads/Runs REST API call) never
show up there, even with Application Insights connected - see
docs/detailed-call-sequence.md. This script reads the same Threads/Runs/
RunSteps data directly from the Foundry Agent Service, which always persists
it regardless of the Traces UI.

USAGE
-----
Run from the repo root (or foundry/ folder) in a Python environment that has
the repo's dependencies installed (azure-ai-agents, azure-identity,
python-dotenv - already installed if you've run any other foundry/*.py
script in this repo). You must be logged in with `az login` as a user/
identity that has the "Foundry User" role on the project (see
docs/OBO_Bot_Teams_Design.docx for role details).

  cd C:\Sagar\MicrosoftIQ\Ins_FNOL_MicrosoftIQ_Logicapps_accelerator
  python foundry\inspect_run_trace.py                     # lists recent threads
  python foundry\inspect_run_trace.py thread_abc123        # shows all runs on a thread
  python foundry\inspect_run_trace.py thread_abc123 run_xyz789   # full trace for one run

With no arguments, it lists the most recent threads (id + created time) so
you can find the one you want to inspect - e.g. the thread created right
after you sent a test email or Teams message. Pass that thread_id to drill
in, then optionally a run_id to see only one run's steps.

OUTPUT
------
For each run step, prints:
  - step type (message_creation or tool_calls)
  - for tool_calls: the tool name, the exact arguments sent, and the raw
    output returned - this is the same information the Traces tab's
    "Trace view" would show, just as plain text.
"""
import itertools
import json
import os
import sys
from datetime import datetime, timezone

# Re-wrap stdout as UTF-8 so emoji in agent responses (used throughout the
# orchestrator's instructions, e.g. the Teams case-alert message) don't crash
# on Windows consoles whose default codepage (cp1252) can't encode them.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
RECENT_THREADS_LIMIT = 15


def _fmt_time(value) -> str:
    """Formats a timestamp that the SDK may return as a datetime or a unix int."""
    if not value:
        return "-"
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def list_recent_threads(client: AgentsClient) -> None:
    print(f"Most recent {RECENT_THREADS_LIMIT} threads (newest first):\n")
    # NOTE: `limit=` on client.threads.list() only sets the per-page size of
    # the underlying paged API call - the SDK's ItemPaged iterator still
    # auto-fetches every subsequent page when iterated directly, so without
    # an explicit cap here `for t in threads` would walk the entire account
    # history. itertools.islice() stops after the first RECENT_THREADS_LIMIT
    # items instead.
    threads = client.threads.list(order=ListSortOrder.DESCENDING, limit=RECENT_THREADS_LIMIT)
    for t in itertools.islice(threads, RECENT_THREADS_LIMIT):
        print(f"  {t.id}    created {_fmt_time(t.created_at)}")
    print("\nRe-run with a thread id to see its runs:")
    print("  python foundry\\inspect_run_trace.py <thread_id>")


def list_runs_on_thread(client: AgentsClient, thread_id: str) -> None:
    print(f"Runs on thread {thread_id} (newest first):\n")
    runs = client.runs.list(thread_id=thread_id, order=ListSortOrder.DESCENDING)
    for r in runs:
        print(
            f"  {r.id}    status={r.status}    "
            f"created {_fmt_time(r.created_at)}    completed {_fmt_time(r.completed_at)}"
        )
    print("\nRe-run with a run id to see its full tool-call trace:")
    print(f"  python foundry\\inspect_run_trace.py {thread_id} <run_id>")


def print_run_trace(client: AgentsClient, thread_id: str, run_id: str) -> None:
    run = client.runs.get(thread_id=thread_id, run_id=run_id)
    print(f"Run {run_id} on thread {thread_id}")
    print(f"  status: {run.status}")
    print(f"  started:   {_fmt_time(run.started_at)}")
    print(f"  completed: {_fmt_time(run.completed_at)}")
    if run.last_error:
        print(f"  last_error: {run.last_error}")
    print()

    steps = client.run_steps.list(thread_id=thread_id, run_id=run_id, order=ListSortOrder.ASCENDING)
    for step in steps:
        print("-" * 80)
        print(f"Step {step.id}  type={step.type}  status={step.status}")
        details = step.step_details
        step_type = getattr(details, "type", None)

        if step_type == "message_creation":
            msg_id = details.message_creation.message_id
            message = client.messages.get(thread_id=thread_id, message_id=msg_id)
            text = "".join(
                part.text.value for part in message.content if getattr(part, "type", None) == "text"
            )
            print(f"  [message_creation] role={message.role}")
            print(f"  {text}")

        elif step_type == "tool_calls":
            for call in details.tool_calls:
                call_type = getattr(call, "type", "unknown")
                print(f"  [tool_call] type={call_type}")
                call_dict = call.as_dict() if hasattr(call, "as_dict") else dict(call)
                # Function-style calls (name/arguments/output) print cleanly
                # as their own fields; everything else (openapi, fabric
                # ontology, azure_ai_search, bing_grounding, ...) is dumped
                # as formatted JSON so no tool type is ever silently hidden.
                function = call_dict.get("function")
                if function:
                    print(f"    name:      {function.get('name')}")
                    print(f"    arguments: {function.get('arguments')}")
                    print(f"    output:    {function.get('output')}")
                else:
                    detail = {k: v for k, v in call_dict.items() if k not in ("id", "type")}
                    print(f"    {json.dumps(detail, indent=2, default=str)}")
        else:
            print(f"  (unrecognized step type: {step_type}) raw: {details}")

    print("-" * 80)


def main():
    client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=AzureCliCredential())

    if len(sys.argv) == 1:
        list_recent_threads(client)
    elif len(sys.argv) == 2:
        list_runs_on_thread(client, sys.argv[1])
    else:
        print_run_trace(client, sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
