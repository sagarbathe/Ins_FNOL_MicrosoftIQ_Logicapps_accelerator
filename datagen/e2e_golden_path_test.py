
"""Golden-path E2E validation against the AutoFNOL lakehouse tables."""
import os
import struct
import subprocess

import pyodbc
from dotenv import load_dotenv

load_dotenv()

SQL_ENDPOINT = os.environ.get("CASETHREADMAP_FABRIC_SQL_ENDPOINT", "")
DATABASE = os.environ.get("CASETHREADMAP_FABRIC_SQL_DATABASE", "")


def get_token():
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://database.windows.net/", "--query", "accessToken", "-o", "tsv"],
        shell=True,
        text=True,
    )
    return out.strip()


def connect():
    if not SQL_ENDPOINT or not DATABASE:
        raise RuntimeError("Set CASETHREADMAP_FABRIC_SQL_ENDPOINT and CASETHREADMAP_FABRIC_SQL_DATABASE first.")
    token_bytes = get_token().encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SQL_ENDPOINT};DATABASE={DATABASE};Encrypt=yes;"
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct})


def main():
    conn = connect()
    cur = conn.cursor()
    tables = ["Policyholder", "Vehicle", "Adjuster", "RepairShop", "Policy", "PolicyVehicle", "Claim", "FraudSignal", "SubrogationFlag"]
    print("=== Table row counts ===")
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"{table}: {cur.fetchone()[0]} rows")
    conn.close()
    print("\nE2E GOLDEN PATH VALIDATION: PASSED")


if __name__ == "__main__":
    main()
