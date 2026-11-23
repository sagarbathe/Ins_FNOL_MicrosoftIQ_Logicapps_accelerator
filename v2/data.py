"""Synthetic, explicitly targeted Fabric provisioning for the isolated v2 workspace."""

import json
import hashlib
import os
import struct
import subprocess
import sys
import time
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyodbc
import requests
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

from v2.ontology_definition import ENTITY_SPECS, RELATIONSHIPS, TABLE_SCHEMAS, build_definition
from v2.deploy import ROOT, STATE, save
from v2.ontology import FabricClient, decode_gen2_definition


def validate_frames(frames):
    if set(frames) != set(TABLE_SCHEMAS):
        raise ValueError("All nine synthetic source tables are required.")
    for name, columns in TABLE_SCHEMAS.items():
        frame = frames[name]
        if set(frame.columns) != set(columns) or frame.empty or frame.isna().any().any():
            raise ValueError(f"{name}: missing rows, values or expected columns.")
    for entity, spec in ENTITY_SPECS.items():
        if not frames[spec["table"]][spec["key"]].is_unique:
            raise ValueError(f"{entity}: entity keys must be unique.")
    if frames["PolicyVehicle"].duplicated().any():
        raise ValueError("Duplicate policy/vehicle junction rows.")
    for _, source, target in RELATIONSHIPS:
        source_table, source_column = source.split(".")
        target_table, target_column = target.split(".")
        if not frames[source_table][source_column].isin(frames[target_table][target_column]).all():
            raise ValueError(f"Broken reference: {source} -> {target}.")
    links = set(frames["PolicyVehicle"].itertuples(index=False, name=None))
    if not set(frames["Claim"][["PolicyId", "VehicleId"]].itertuples(index=False, name=None)) <= links:
        raise ValueError("A claim vehicle is not covered by its policy.")


def prepare_data():
    directory = STATE.parent / "synthetic"
    directory.mkdir(parents=True, exist_ok=True)
    if not any(directory.glob("*.csv")):
        environment = dict(os.environ, DATAGEN_OUTPUT_DIR=str(directory))
        subprocess.run(
            [sys.executable, str(ROOT / "v2" / "generate_data.py")],
            env=environment, check=True,
        )
    dtypes = {"string": "string", "int64": "int64", "double": "float64", "boolean": "bool"}
    frames = {
        name: pd.read_csv(
            directory / f"{name}.csv", keep_default_na=False,
            dtype={column: dtypes[kind] for column, kind in columns.items()},
        ) for name, columns in TABLE_SCHEMAS.items()
    }
    validate_frames(frames)
    arrow_types = {"string": pa.string(), "int64": pa.int64(), "double": pa.float64(), "boolean": pa.bool_()}
    for name, frame in frames.items():
        schema = pa.schema([
            pa.field(column, arrow_types[kind], nullable=False)
            for column, kind in TABLE_SCHEMAS[name].items()
        ])
        pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False),
                       directory / f"{name}.parquet")
    return directory, {name: len(frame) for name, frame in frames.items()}


def durable_write(client, state, key, path, body, *, result=True):
    operations = state.setdefault("fabricOperations", {})
    operation = operations.get(key)
    if operation and operation["status"] == "Succeeded":
        return operation.get("result")
    if operation:
        response = requests.Response()
        response.status_code = 202
        response.headers["x-ms-operation-id"] = operation["id"]
    else:
        response = client.request("POST", path, body)
        if response.status_code == 202:
            operations[key] = {"id": str(uuid.UUID(response.headers["x-ms-operation-id"])), "status": "Running"}
            save(state)
    value = client.complete(response, result=result)
    operations[key] = {**operations.get(key, {}), "status": "Succeeded", "result": value}
    save(state)
    return value


def provision_fabric_data(state):
    directory, counts = prepare_data()
    credential = AzureCliCredential(tenant_id=state["tenantId"])
    client = FabricClient(credential)
    capacity = state["fabricCapacity"]
    live_capacity = client.request("GET", "/capacities/" + str(uuid.UUID(capacity["id"]))).json()
    if live_capacity.get("state") != "Active":
        raise RuntimeError("The explicitly approved Fabric capacity is not active.")
    marker = "fnol-delegated-v2:" + state["environmentId"]
    workspace_name = state["prefix"] + "-fabric-v2"
    matches = [item for item in client.list_all("/workspaces") if item["displayName"] == workspace_name]
    if len(matches) > 1 or (matches and matches[0].get("description") != marker):
        raise RuntimeError("Refusing to adopt an ambiguous or unowned workspace.")
    workspace = matches[0] if matches else durable_write(
        client, state, "workspace", "/workspaces",
        {"displayName": workspace_name, "description": marker, "capacityId": capacity["id"]},
    )
    workspace = client.request("GET", "/workspaces/" + workspace["id"]).json()
    if workspace.get("description") != marker or workspace.get("capacityId") != capacity["id"]:
        raise RuntimeError("Workspace ownership or capacity readback does not match.")
    fabric = state.setdefault("fabric", {})
    fabric["workspaceId"] = workspace["id"]
    save(state)
    base = "/workspaces/" + workspace["id"] + "/lakehouses"
    lakehouse_name = "LH_FNOL_Delegated_V2"
    matches = [item for item in client.list_all(base) if item["displayName"] == lakehouse_name]
    if len(matches) > 1 or (matches and matches[0].get("description") != marker):
        raise RuntimeError("Refusing to adopt an ambiguous or unowned lakehouse.")
    lakehouse = matches[0] if matches else durable_write(
        client, state, "lakehouse", base, {"displayName": lakehouse_name, "description": marker},
    )
    fabric["lakehouseId"] = lakehouse["id"]
    save(state)
    lakehouse = client.request("GET", base + "/" + lakehouse["id"]).json()
    if lakehouse.get("description") != marker or lakehouse.get("workspaceId") != workspace["id"]:
        raise RuntimeError("Lakehouse readback does not match the owned workspace.")
    if lakehouse.get("properties", {}).get("defaultSchema"):
        raise RuntimeError("This loader requires a non-schema lakehouse; no schema fallback is permitted.")
    files = DataLakeServiceClient(
        "https://onelake.dfs.fabric.microsoft.com", credential=credential,
    ).get_file_system_client(workspace["id"])
    source = lakehouse["id"] + "/Files/v2synthetic"
    files.create_directory(source)
    for name in TABLE_SCHEMAS:
        path = directory / f"{name}.parquet"
        with path.open("rb") as stream:
            files.get_file_client(source + "/" + path.name).upload_data(stream, overwrite=True)
        durable_write(
            client, state, "load-" + name, base + "/" + lakehouse["id"] + f"/tables/{name}/load",
            {"relativePath": f"Files/v2synthetic/{name}.parquet", "pathType": "File",
             "mode": "Overwrite", "formatOptions": {"format": "Parquet"}}, result=False,
        )
        print(f"Loaded synthetic {name}: expected {counts[name]} rows.", flush=True)
    fabric["expectedCounts"] = counts
    save(state)
    verify_sql_counts(client, credential, state)
    lakehouse = client.request("GET", base + "/" + lakehouse["id"]).json()
    endpoint = lakehouse["properties"]["sqlEndpointProperties"]
    definition = build_definition(
        ontology_name="FNOL_Delegated_Gen2", workspace_id=workspace["id"],
        lakehouse_id=lakehouse["id"], connection_string=endpoint["connectionString"],
        database_id=endpoint["id"], lakehouse_name=lakehouse["displayName"],
    )
    decoded = decode_gen2_definition(definition)
    encoded = json.dumps(definition, indent=2).encode("utf-8")
    (STATE.parent / "ontology.definition.json").write_bytes(encoded)
    fabric["ontologyPreview"] = {
        "name": "FNOL_Delegated_Gen2", "parts": sorted(decoded), "generationRequired": 2,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    save(state)
    print("All nine SQL row counts verified; Gen 2 definition prepared. Ontology creation requires preview approval.")


def verify_sql_counts(client, credential, state):
    fabric = state["fabric"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        lakehouse = client.request(
            "GET", f"/workspaces/{fabric['workspaceId']}/lakehouses/{fabric['lakehouseId']}",
        ).json()
        endpoint = lakehouse["properties"].get("sqlEndpointProperties", {})
        if endpoint.get("provisioningStatus") != "Success" or not endpoint.get("connectionString"):
            time.sleep(10)
            continue
        if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
            raise RuntimeError("ODBC Driver 18 is required for actual lakehouse row-count verification.")
        token = credential.get_token("https://database.windows.net/.default").token.encode("utf-16-le")
        connection = pyodbc.connect(
            "DRIVER={ODBC Driver 18 for SQL Server};SERVER=" + endpoint["connectionString"]
            + ";DATABASE=" + endpoint["id"] + ";Encrypt=yes;TrustServerCertificate=no;",
            attrs_before={1256: struct.pack("<I", len(token)) + token}, timeout=30,
        )
        try:
            with connection:
                counts = {
                    name: connection.execute(f"SELECT COUNT_BIG(*) FROM [dbo].[{name}]").fetchval()
                    for name in TABLE_SCHEMAS
                }
            if counts != fabric["expectedCounts"]:
                raise RuntimeError(f"Lakehouse counts do not match synthetic input: {counts}.")
            fabric["verifiedCounts"] = counts
            save(state)
            return
        except pyodbc.ProgrammingError as error:
            if error.args[0] != "42S02":
                raise
        finally:
            connection.close()
        time.sleep(10)
    raise TimeoutError("SQL endpoint tables are not synchronized; rerun verification, not table creation.")


def seed_private_cases(state):
    from v2.bootstrap import seed_inside_app
    from v2.provision import Rest, clients
    from cryptography.fernet import Fernet

    fabric = state["fabric"]
    if not fabric.get("verifiedCounts") or fabric["verifiedCounts"] != fabric.get("expectedCounts"):
        raise ValueError("Verified synthetic Fabric data is required before case assignment.")
    credential, _, _ = clients(state)
    selection = state["vaultSelection"]
    secret = Rest(credential, selection["url"], "https://vault.azure.net/.default").call(
        "GET", "/secrets/" + selection["secretPrefix"] + "-token-encryption-key?api-version=7.4",
        sensitive=True,
    )
    if secret.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The case encryption key does not belong to this v2 environment.")
    claim_ids = pd.read_csv(
        STATE.parent / "synthetic" / "Claim.csv", usecols=["ClaimId"], dtype="string",
    )["ClaimId"].head(3).tolist()
    cases = [{
        "id": claim_id, "claim_id": claim_id, "title": "Synthetic claim " + claim_id,
        "tenant_id": state["tenantId"], "allowed_users": [state["operatorId"]],
        "environment_id": state["environmentId"],
    } for claim_id in claim_ids]
    cases.append({
        "id": "V2-ACCESS-CONTROL", "claim_id": claim_ids[0], "title": "Unassigned access control",
        "tenant_id": state["tenantId"], "allowed_users": [], "environment_id": state["environmentId"],
    })
    seed_inside_app(state, cases, Fernet(secret["value"].encode("ascii")))
    state["caseBootstrap"] = {"assigned": claim_ids, "unassignedControl": "V2-ACCESS-CONTROL"}
    save(state)
    print("Three encrypted synthetic cases assigned to the pilot operator; unassigned denial control created.")
