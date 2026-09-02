"""
Creates the CaseThreadMap table (+ its unique index) in the Fabric SQL
Database used by the concurrency/Teams-thread-isolation design (see
docs/teams-concurrency-design.md and shared/case_thread_store_schema.sql).

Idempotent - safe to re-run; only creates the table/index if missing.

Authenticates using the Agent Identity's SQL-scoped Entra token
(SQL_COPT_SS_ACCESS_TOKEN) via shared/agent_identity_auth.py - NOT a SQL
login/password - so shared/bootstrap_agent_identity_tokens.py must have been
run at least once first (to acquire and cache an Azure SQL Database scope).

Usage:
    python shared/create_case_thread_map.py

Reads CASETHREADMAP_FABRIC_SQL_ENDPOINT and
CASETHREADMAP_FABRIC_SQL_DATABASE from .env.
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
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'CaseThreadMap')
BEGIN
    CREATE TABLE CaseThreadMap (
        CaseId                  NVARCHAR(256) NOT NULL PRIMARY KEY,
        TeamId                  NVARCHAR(64)  NOT NULL,
        ChannelId               NVARCHAR(128) NOT NULL,
        RootMessageId           NVARCHAR(128) NOT NULL,
        FoundryThreadId         NVARCHAR(128) NOT NULL,
        CreatedUtc              DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        LastActivityUtc         DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        LastProcessedReplyId    NVARCHAR(128) NULL,
        LastProcessedReplyUtc   DATETIME2     NULL
    )
END
"""

SCHEMA_SQL_ADD_COLUMNS = """
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('CaseThreadMap') AND name = 'LastProcessedReplyId')
BEGIN
    ALTER TABLE CaseThreadMap ADD LastProcessedReplyId NVARCHAR(128) NULL
END
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('CaseThreadMap') AND name = 'LastProcessedReplyUtc')
BEGIN
    ALTER TABLE CaseThreadMap ADD LastProcessedReplyUtc DATETIME2 NULL
END
"""

SCHEMA_SQL_INDEX = """
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_CaseThreadMap_TeamsThread')
BEGIN
    CREATE UNIQUE INDEX IX_CaseThreadMap_TeamsThread
        ON CaseThreadMap (TeamId, ChannelId, RootMessageId)
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
    cur.execute(SCHEMA_SQL_ADD_COLUMNS)
    conn.commit()
    cur.execute(SCHEMA_SQL_INDEX)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM CaseThreadMap")
    print("CaseThreadMap row count:", cur.fetchone()[0])
    conn.close()
    print("Schema applied successfully.")


if __name__ == "__main__":
    main()
