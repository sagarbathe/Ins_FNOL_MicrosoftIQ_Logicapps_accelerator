r"""
Syncs Foundry orchestrator Threads/Runs/RunSteps into the AgentRunHistory
Fabric SQL table (see shared/agent_run_history_schema.sql and
docs/agent-governance-observability-mvp.md section 3), so run/tool-call
history is queryable with plain SQL instead of re-running
foundry/inspect_run_trace.py by hand every time.

Reads Foundry the same way inspect_run_trace.py does (AgentsClient +
AzureCliCredential - run `az login` first as a user with the "Foundry User"
role on the project). Writes to the same Fabric SQL Database that already
hosts CaseThreadMap, using the Agent Identity's SQL-scoped token (same
pattern as shared/create_case_thread_map.py - run
shared/bootstrap_agent_identity_tokens.py first if you haven't already).

Idempotent: every row is upserted keyed on (ThreadId, RunId, StepId) - or
(ThreadId, RunId) with StepId NULL for the synthetic "user_message" row -
via the unique indexes in the schema, so re-running this script never
duplicates rows; it only fills in threads/runs/steps it hasn't seen yet.

USAGE
-----
  cd C:\Sagar\MicrosoftIQ\Ins_FNOL_MicrosoftIQ_Logicapps_accelerator
  python foundry\sync_run_history.py                  # sync the N most recent threads (default 30)
  python foundry\sync_run_history.py --limit 100       # sync more threads
  python foundry\sync_run_history.py thread_abc123      # sync just one thread

NOTES
-----
- SourceChannel is best-effort: 'case' if the thread is found in
  CaseThreadMap (i.e. it's a real email/Teams case, either intake or a
  Teams-continued follow-up), else 'unknown' (most likely a Foundry
  Playground test conversation, or a connected-agent's internal sub-thread
  before its ParentThreadId backfill runs - see below). CaseThreadMap does
  not currently distinguish "email" vs "teams" as separate values, so this
  script does not invent that distinction either.
- ParentThreadId is backfilled in a second pass: while walking tool_calls
  steps, any `connected_agent` call's output includes the *child* thread_id/
  run_id it spawned (e.g. Foundry IQ's internal sub-thread) - this script
  records those links and, after processing all requested threads, updates
  every already-inserted row for that child thread with ParentThreadId set.
"""
import argparse
import itertools
import json
import os
import struct
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pyodbc
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder
from azure.identity import AzureCliCredential

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
from agent_identity_auth import _app, _load_cache  # noqa: E402

PROJECT_ENDPOINT = config.FOUNDRY_PROJECT_ENDPOINT
DEFAULT_THREAD_LIMIT = 30
RUNS_PER_THREAD_LIMIT = 100
SQL_SCOPE = ["https://database.windows.net/.default"]
SQL_COPT_SS_ACCESS_TOKEN = 1256
SERVER = os.environ["CASETHREADMAP_FABRIC_SQL_ENDPOINT"]
DATABASE = os.environ["CASETHREADMAP_FABRIC_SQL_DATABASE"]


def _to_dt(value):
    """Normalizes a Foundry SDK timestamp (datetime or unix int) to an
    aware UTC datetime, matching inspect_run_trace.py's _fmt_time handling.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _get_sql_connection():
    cache = _load_cache()
    app = _app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "No cached Agent Identity account found. Run "
            "python shared/bootstrap_agent_identity_tokens.py first."
        )
    result = app.acquire_token_silent(SQL_SCOPE, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(f"Failed to get a SQL token: {result}")
    token_bytes = result["access_token"].encode("utf-16-le")
    token_struct = struct.pack("=i", len(token_bytes)) + token_bytes
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def _lookup_case_id(cursor, thread_id: str):
    cursor.execute("SELECT CaseId FROM CaseThreadMap WHERE FoundryThreadId = ?", (thread_id,))
    row = cursor.fetchone()
    return (row[0], "case") if row else (None, "unknown")


_UPSERT_STEP_SQL = """
IF NOT EXISTS (
    SELECT 1 FROM AgentRunHistory
    WHERE ThreadId = ? AND RunId = ? AND StepId = ?
)
INSERT INTO AgentRunHistory
    (CaseId, ThreadId, ParentThreadId, RunId, StepId, StepIndex, RecordType,
     ToolName, ToolType, Prompt, Response, RunStatus, ErrorMessage,
     AssistantId, PromptTokens, CompletionTokens, SourceChannel, CreatedAtUtc)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPSERT_USER_MESSAGE_SQL = """
IF NOT EXISTS (
    SELECT 1 FROM AgentRunHistory
    WHERE ThreadId = ? AND RunId = ? AND StepId IS NULL
)
INSERT INTO AgentRunHistory
    (CaseId, ThreadId, ParentThreadId, RunId, StepId, StepIndex, RecordType,
     ToolName, ToolType, Prompt, Response, RunStatus, ErrorMessage,
     AssistantId, PromptTokens, CompletionTokens, SourceChannel, CreatedAtUtc)
VALUES (?, ?, NULL, ?, NULL, NULL, 'user_message', NULL, NULL, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
"""


def _message_text(message) -> str:
    return "".join(
        part.text.value for part in message.content if getattr(part, "type", None) == "text"
    )


def sync_thread(client: AgentsClient, cursor, thread_id: str, child_thread_links: dict) -> int:
    """Syncs every run/step for one thread. Returns the number of new rows
    inserted. Populates `child_thread_links[child_thread_id] = parent_thread_id`
    whenever a connected_agent tool call is found, for the second-pass
    ParentThreadId backfill in main().
    """
    case_id, source_channel = _lookup_case_id(cursor, thread_id)

    # pyodbc's cursor.rowcount is unreliable for conditional
    # "IF NOT EXISTS ... INSERT" statements, so count before/after instead
    # of trying to sum rowcount per statement.
    cursor.execute("SELECT COUNT(*) FROM AgentRunHistory WHERE ThreadId = ?", (thread_id,))
    count_before = cursor.fetchone()[0]

    messages = list(client.messages.list(thread_id=thread_id, order=ListSortOrder.ASCENDING))
    runs = list(
        itertools.islice(
            client.runs.list(thread_id=thread_id, order=ListSortOrder.ASCENDING, limit=RUNS_PER_THREAD_LIMIT),
            RUNS_PER_THREAD_LIMIT,
        )
    )

    msg_idx = 0

    for run in runs:
        run_created = _to_dt(run.created_at)

        # Attribute the nearest unclaimed preceding user message to this run
        # (see module docstring: one user message always precedes one run
        # in this architecture's Logic-App-driven flow).
        claimed_user_message = None
        while msg_idx < len(messages):
            m = messages[msg_idx]
            if str(m.role).endswith("USER") or str(m.role) == "user":
                m_created = _to_dt(m.created_at)
                if m_created and run_created and m_created <= run_created:
                    claimed_user_message = m
                    msg_idx += 1
                    continue
                break
            msg_idx += 1

        usage = getattr(run, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        last_error = getattr(run, "last_error", None)
        error_message = json.dumps(last_error, default=str) if last_error else None

        if claimed_user_message is not None:
            cursor.execute(
                _UPSERT_USER_MESSAGE_SQL,
                (
                    thread_id, run.id,  # IF NOT EXISTS check
                    case_id, thread_id, run.id,
                    _message_text(claimed_user_message),
                    source_channel, _to_dt(claimed_user_message.created_at),
                ),
            )

        steps = client.run_steps.list(thread_id=thread_id, run_id=run.id, order=ListSortOrder.ASCENDING)
        for step_index, step in enumerate(steps):
            details = step.step_details
            step_type = getattr(details, "type", None)
            step_created = _to_dt(step.created_at) or run_created

            if step_type == "message_creation":
                msg_id = details.message_creation.message_id
                message = client.messages.get(thread_id=thread_id, message_id=msg_id)
                cursor.execute(
                    _UPSERT_STEP_SQL,
                    (
                        thread_id, run.id, step.id,  # IF NOT EXISTS check
                        case_id, thread_id, None, run.id, step.id, step_index, "agent_message",
                        None, None, None, _message_text(message),
                        str(run.status), error_message, run.agent_id,
                        prompt_tokens, completion_tokens, source_channel, step_created,
                    ),
                )

            elif step_type == "tool_calls":
                for call in details.tool_calls:
                    call_type = getattr(call, "type", "unknown")
                    call_dict = call.as_dict() if hasattr(call, "as_dict") else dict(call)
                    function = call_dict.get("function")
                    if function:
                        tool_name = function.get("name")
                        prompt_text = function.get("arguments")
                        response_text = function.get("output")
                    else:
                        nested = call_dict.get(call_type, {})
                        tool_name = nested.get("name", call_type)
                        prompt_text = json.dumps(
                            {k: v for k, v in nested.items() if k not in ("output", "name")}, default=str
                        )
                        response_text = nested.get("output")
                        if call_type == "connected_agent":
                            child_thread_id = nested.get("thread_id")
                            if child_thread_id:
                                child_thread_links[child_thread_id] = thread_id

                    step_id = f"{step.id}:{call_dict.get('id', call_type)}"
                    cursor.execute(
                        _UPSERT_STEP_SQL,
                        (
                            thread_id, run.id, step_id,  # IF NOT EXISTS check
                            case_id, thread_id, None, run.id, step_id,
                            step_index, "tool_call", tool_name, call_type, prompt_text, response_text,
                            str(run.status), error_message, run.agent_id,
                            prompt_tokens, completion_tokens, source_channel, step_created,
                        ),
                    )

    cursor.execute("SELECT COUNT(*) FROM AgentRunHistory WHERE ThreadId = ?", (thread_id,))
    count_after = cursor.fetchone()[0]
    return count_after - count_before


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("thread_id", nargs="?", help="Sync only this thread id (default: sync recent threads)")
    parser.add_argument("--limit", type=int, default=DEFAULT_THREAD_LIMIT, help="How many recent threads to sync")
    args = parser.parse_args()

    client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=AzureCliCredential())
    conn = _get_sql_connection()
    cursor = conn.cursor()

    if args.thread_id:
        thread_ids = [args.thread_id]
    else:
        threads = client.threads.list(order=ListSortOrder.DESCENDING, limit=args.limit)
        thread_ids = [t.id for t in itertools.islice(threads, args.limit)]

    child_thread_links = {}
    total_inserted = 0
    for thread_id in thread_ids:
        n = sync_thread(client, cursor, thread_id, child_thread_links)
        conn.commit()
        total_inserted += n
        print(f"  {thread_id}: +{n} new row(s)")

    # Second pass: backfill ParentThreadId for any connected-agent child
    # threads discovered above.
    for child_thread_id, parent_thread_id in child_thread_links.items():
        cursor.execute(
            "UPDATE AgentRunHistory SET ParentThreadId = ? WHERE ThreadId = ? AND ParentThreadId IS NULL",
            (parent_thread_id, child_thread_id),
        )
    conn.commit()

    print(f"\nTotal new rows inserted: {total_inserted}")
    if child_thread_links:
        print(f"Backfilled ParentThreadId for {len(child_thread_links)} connected-agent sub-thread(s).")
    conn.close()


if __name__ == "__main__":
    main()
