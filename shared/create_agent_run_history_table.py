"""
Creates the AgentRunHistory table (+ its indexes) in the same Fabric SQL
Database that already hosts CaseThreadMap (see
shared/agent_run_history_schema.sql for the full design rationale and
docs/agent-governance-observability-mvp.md section 3 for the overall MVP).

Idempotent - safe to re-run; only creates the table/indexes if missing.

Authenticates using the Agent Identity's SQL-scoped Entra token
(SQL_COPT_SS_ACCESS_TOKEN) via shared/agent_identity_auth.py - NOT a SQL
login/password - so shared/bootstrap_agent_identity_tokens.py must have been
run at least once first (to acquire and cache an Azure SQL Database scope).
This is the same pattern as shared/create_case_thread_map.py.

Usage:
    python shared/create_agent_run_history_table.py

Reads CASETHREADMAP_FABRIC_SQL_ENDPOINT and
CASETHREADMAP_FABRIC_SQL_DATABASE from .env (same Fabric SQL DB, no new
connection settings needed).
"""
import os
import struct
import sys

import pyodbc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from agent_identity_auth import _app, _load_cache  # noqa: E402

SQL_SCOPE = ["https://database.windows.net/.default"]
SQL_COPT_SS_ACCESS_TOKEN = 1256

SERVER = os.environ["CASETHREADMAP_FABRIC_SQL_ENDPOINT"]
DATABASE = os.environ["CASETHREADMAP_FABRIC_SQL_DATABASE"]

SCHEMA_SQL_TABLE = """
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'AgentRunHistory')
BEGIN
    CREATE TABLE AgentRunHistory (
        Id                  BIGINT IDENTITY(1,1) PRIMARY KEY,
        CaseId              NVARCHAR(256) NULL,
        ThreadId            NVARCHAR(128) NOT NULL,
        ParentThreadId      NVARCHAR(128) NULL,
        RunId               NVARCHAR(128) NOT NULL,
        StepId              NVARCHAR(128) NULL,
        StepIndex           INT NULL,
        RecordType          NVARCHAR(20)  NOT NULL,
        ToolName            NVARCHAR(200) NULL,
        ToolType            NVARCHAR(50)  NULL,
        Prompt              NVARCHAR(MAX) NULL,
        Response            NVARCHAR(MAX) NULL,
        RunStatus           NVARCHAR(30)  NULL,
        ErrorMessage        NVARCHAR(MAX) NULL,
        AssistantId         NVARCHAR(128) NULL,
        PromptTokens        INT NULL,
        CompletionTokens    INT NULL,
        SourceChannel       NVARCHAR(30)  NULL,
        CreatedAtUtc        DATETIME2     NOT NULL,
        IngestedAtUtc       DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    )
END
"""

SCHEMA_SQL_INDEXES = """
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_AgentRunHistory_ThreadRunStep')
BEGIN
    CREATE UNIQUE INDEX IX_AgentRunHistory_ThreadRunStep
        ON AgentRunHistory (ThreadId, RunId, StepId)
        WHERE StepId IS NOT NULL
END
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_AgentRunHistory_ThreadRunUserMessage')
BEGIN
    CREATE UNIQUE INDEX IX_AgentRunHistory_ThreadRunUserMessage
        ON AgentRunHistory (ThreadId, RunId)
        WHERE StepId IS NULL
END
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_AgentRunHistory_CaseId')
BEGIN
    CREATE INDEX IX_AgentRunHistory_CaseId ON AgentRunHistory (CaseId)
END
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_AgentRunHistory_ParentThreadId')
BEGIN
    CREATE INDEX IX_AgentRunHistory_ParentThreadId ON AgentRunHistory (ParentThreadId)
END
"""


def _get_sql_token() -> str:
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
        raise RuntimeError(
            f"Failed to get a SQL token: {result}. If this is the first run, "
            "make sure shared/bootstrap_agent_identity_tokens.py included the "
            "Azure SQL Database scope."
        )
    return result["access_token"]


def main():
    token = _get_sql_token()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack("=i", len(token_bytes)) + token_bytes

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL_TABLE)
    conn.commit()
    cur.execute(SCHEMA_SQL_INDEXES)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM AgentRunHistory")
    print("AgentRunHistory row count:", cur.fetchone()[0])
    conn.close()
    print("Schema applied successfully.")


if __name__ == "__main__":
    main()
