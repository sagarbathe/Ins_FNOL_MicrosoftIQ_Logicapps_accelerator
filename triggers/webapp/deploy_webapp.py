"""
Deploys (or redeploys) the Work IQ webapp to Azure App Service and pushes
all required app settings from .env.

Usage:
    python triggers/webapp/deploy_webapp.py

Requires:
    - `az login` already done, correct subscription selected (`az account set`)
    - .env fully populated (this script pushes its own values as app settings
      so secrets never need to be typed into the `az` CLI by hand)

Notes:
    - If the App Service Plan / Web App (WORKIQ_RESOURCE_GROUP /
      WORKIQ_WEBAPP_NAME in .env) do not already exist, this script creates
      them automatically (Linux, Python, SKU from WORKIQ_SKU or B1 default,
      location from WORKIQ_LOCATION or westus3 default) before deploying code.
"""
import json
import os
import subprocess
import sys
import tempfile
import zipfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config  # noqa: E402 (loads .env via python-dotenv)

AZ_CMD = shutil.which("az") or ("az.cmd" if os.name == "nt" else "az")

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(WEBAPP_DIR, "..", "..")

RESOURCE_GROUP = os.environ.get("WORKIQ_RESOURCE_GROUP", "")
WEBAPP_NAME = os.environ.get("WORKIQ_WEBAPP_NAME", "app-autofnol-workiq")
APP_SERVICE_PLAN = os.environ.get("WORKIQ_APP_SERVICE_PLAN", f"asp-{WEBAPP_NAME.replace('app-', '')}")
LOCATION = os.environ.get("WORKIQ_LOCATION", "westus3")
SKU = os.environ.get("WORKIQ_SKU", "B1")
PYTHON_VERSION = os.environ.get("WORKIQ_PYTHON_VERSION", "PYTHON:3.11")

FILES_TO_PACKAGE = [
    "app.py",
    "tools_fabric_iq.py",
    "tools_workiq_graph.py",
    "agent_identity_auth.py",
    "post_to_teams.py",
    "requirements.txt",
]

APP_SETTINGS_FROM_ENV = [
    "WORKIQ_API_KEY",
    "AGENT_IDENTITY_PUBLIC_CLIENT_ID",
    "AGENT_IDENTITY_TENANT_ID",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_DATA_AGENT_ID",
    "CASETHREADMAP_FABRIC_SQL_CONNECTION_STRING",
    "GRAPH_SEARCH_REGION",
    "AUTH_MODE",
    "AAD_TENANT_ID",
    "AAD_CLIENT_ID",
    "AAD_CLIENT_SECRET",
    "TEAMS_TEAM_ID",
    "TEAMS_CHANNEL_ID",
]


def run(cmd: list, **kwargs):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)


def ensure_app_service_exists():
    """Creates the App Service Plan + Web App if they don't already exist
    (idempotent - safe to re-run)."""
    check = subprocess.run(
        [AZ_CMD, "group", "exists", "-n", RESOURCE_GROUP],
        capture_output=True, text=True,
    )
    if check.stdout.strip().lower() != "true":
        print(f"Resource group '{RESOURCE_GROUP}' not found - creating it in {LOCATION}.")
        run([AZ_CMD, "group", "create", "-n", RESOURCE_GROUP, "-l", LOCATION])

    plan_check = subprocess.run(
        [AZ_CMD, "appservice", "plan", "show", "-g", RESOURCE_GROUP, "-n", APP_SERVICE_PLAN],
        capture_output=True, text=True,
    )
    if plan_check.returncode != 0:
        print(f"App Service Plan '{APP_SERVICE_PLAN}' not found - creating it (Linux, SKU {SKU}).")
        run([
            AZ_CMD, "appservice", "plan", "create",
            "-g", RESOURCE_GROUP, "-n", APP_SERVICE_PLAN,
            "--is-linux", "--sku", SKU, "-l", LOCATION,
        ])
    else:
        print(f"App Service Plan '{APP_SERVICE_PLAN}' already exists - skipping creation.")

    app_check = subprocess.run(
        [AZ_CMD, "webapp", "show", "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME],
        capture_output=True, text=True,
    )
    if app_check.returncode != 0:
        print(f"Web App '{WEBAPP_NAME}' not found - creating it (runtime {PYTHON_VERSION}).")
        run([
            AZ_CMD, "webapp", "create",
            "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME,
            "--plan", APP_SERVICE_PLAN, "--runtime", PYTHON_VERSION,
        ])
        # Startup command for a Flask app served via gunicorn (adjust if
        # app.py uses a different entry point/framework). Uses 4 threads on
        # a single worker (not multiple worker processes) so all requests
        # share one in-process token/connection cache; --timeout 600
        # tolerates the ~20-30s Fabric SQL / ontology query latency seen in
        # testing without the worker being killed mid-request.
        run([
            AZ_CMD, "webapp", "config", "set",
            "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME,
            "--startup-file", "gunicorn --bind=0.0.0.0 --timeout 600 --workers 1 --threads 4 app:app",
        ])
    else:
        print(f"Web App '{WEBAPP_NAME}' already exists - skipping creation.")


def main():
    if not RESOURCE_GROUP:
        raise SystemExit("Set WORKIQ_RESOURCE_GROUP in .env before running this script.")

    ensure_app_service_exists()

    # 1. Package + deploy code
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "workiq_deploy.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in FILES_TO_PACKAGE:
                fpath = os.path.join(WEBAPP_DIR, fname)
                if not os.path.exists(fpath):
                    raise SystemExit(f"Missing expected webapp file: {fpath}")
                zf.write(fpath, arcname=fname)

        run([
            AZ_CMD, "webapp", "deploy",
            "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME,
            "--src-path", zip_path, "--type", "zip",
        ])

    # 2. Push app settings (never printed/echoed in full - values come
    #    straight from the environment, not typed on the command line by a
    #    human, but note `az` itself may still log the command with values in
    #    verbose/debug modes - avoid `--debug` when running this script).
    settings_args = []
    missing = []
    for key in APP_SETTINGS_FROM_ENV:
        val = os.environ.get(key, "")
        if not val:
            missing.append(key)
            continue
        settings_args.append(f"{key}={val}")

    # Seed the Agent Identity token cache as base64 app setting(s). Azure App
    # Service enforces a hard 50,000-character limit per app setting VALUE
    # (this is an ARM constraint, not something az/this script can raise) -
    # the token cache can exceed that once it holds Graph+Fabric+SQL scopes
    # (e.g. after adding Mail.Send it grew past 50,000 chars and started
    # failing outright). To stay correct regardless of size, split it into
    # fixed-size chunks (AGENT_IDENTITY_TOKEN_CACHE_B64_0, _1, _2, ...) plus a
    # count setting, and reassemble them in agent_identity_auth.py.
    b64_path = os.path.join(REPO_ROOT, ".secrets", "token_cache_b64.txt")
    if os.path.exists(b64_path):
        with open(b64_path, "r", encoding="ascii") as f:
            b64_value = f.read().strip()
        CHUNK_SIZE = 40000  # comfortably under the 50,000-char app setting limit
        chunks = [b64_value[i:i + CHUNK_SIZE] for i in range(0, len(b64_value), CHUNK_SIZE)] or [""]
        for i, chunk in enumerate(chunks):
            settings_args.append(f"AGENT_IDENTITY_TOKEN_CACHE_B64_{i}={chunk}")
        settings_args.append(f"AGENT_IDENTITY_TOKEN_CACHE_B64_COUNT={len(chunks)}")
        print(f"Token cache is {len(b64_value)} chars - split into {len(chunks)} app setting(s).")
    else:
        print(
            "WARNING: .secrets/token_cache_b64.txt not found - run "
            "shared/bootstrap_agent_identity_tokens.py first, then rerun this "
            "script, or the webapp will have no Agent Identity token cache."
        )

    if settings_args:
        # The base64 token cache can be tens of KB, which blows past Windows'
        # CreateProcess command-line length limit (~32K chars) if passed
        # inline. Write all settings to a temp JSON file instead and use
        # 'az ... --settings @file.json', which az supports for any
        # --settings/--slot-settings argument.
        settings_json = [
            {"name": kv.split("=", 1)[0], "value": kv.split("=", 1)[1]}
            for kv in settings_args
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(settings_json, f)
            settings_file = f.name
        try:
            run([
                AZ_CMD, "webapp", "config", "appsettings", "set",
                "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME,
                "--settings", f"@{settings_file}",
            ])
        finally:
            os.remove(settings_file)

    if missing:
        print(f"\nWARNING: the following .env values were empty and were NOT pushed as app "
              f"settings (fill them in .env and rerun if needed): {', '.join(missing)}")

    # 3. Verify
    print("\nVerifying deployment...")
    run([AZ_CMD, "webapp", "restart", "-g", RESOURCE_GROUP, "-n", WEBAPP_NAME])
    print(f"\nDone. Check health at: https://{WEBAPP_NAME}.azurewebsites.net/healthz")


if __name__ == "__main__":
    main()
