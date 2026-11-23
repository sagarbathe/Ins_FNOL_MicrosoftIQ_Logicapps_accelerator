"""Isolated v2 deployment. Run from repository root with python -m v2.deploy."""

import argparse
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".secrets" / "v2" / "deployment.json"


def az(*arguments):
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI is required.")
    process = subprocess.run(
        [executable, *arguments, "--only-show-errors", "--output", "json"],
        text=True, capture_output=True, check=False,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip())
    return json.loads(process.stdout) if process.stdout.strip() else None


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE)


def load():
    if not STATE.exists():
        raise RuntimeError("Run the init stage with explicit tenant and subscription first.")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    account = az("account", "show")
    if (account["tenantId"] != state["tenantId"]
            or account["id"] != state["subscriptionId"]):
        raise RuntimeError("Azure CLI tenant/subscription does not match the isolated v2 state.")
    return state


def init(args):
    if STATE.exists():
        raise RuntimeError("Deployment state already exists; it must not be reinitialized.")
    account = az("account", "show")
    tenant = str(uuid.UUID(args.tenant))
    subscription = str(uuid.UUID(args.subscription))
    if account["tenantId"] != tenant or account["id"] != subscription:
        raise RuntimeError("Sign in to the explicitly selected tenant and subscription first.")
    if not re.fullmatch(r"[a-z][a-z0-9]{5,15}", args.prefix):
        raise ValueError("Prefix must contain 6-16 lowercase alphanumeric characters.")
    operator = az("ad", "signed-in-user", "show")["id"]
    environment = str(uuid.uuid4())
    state = {
        "tenantId": tenant, "subscriptionId": subscription, "operatorId": operator,
        "environmentId": environment, "prefix": args.prefix, "location": args.location,
        "resourceGroup": f"rg-{args.prefix}-v2",
        "protectedWorkflows": [],
    }
    if az("group", "exists", "--name", state["resourceGroup"]):
        raise RuntimeError("Refusing to adopt an existing resource group.")
    save(state)
    print("Initialized isolated deployment state under .secrets\\v2.")


def foundation(state, args):
    name = state["resourceGroup"]
    if az("group", "exists", "--name", name):
        group = az("group", "show", "--name", name)
        if group.get("tags", {}).get("environmentId") != state["environmentId"]:
            raise RuntimeError("Resource group ownership marker does not match.")
    else:
        az("group", "create", "--name", name, "--location", state["location"],
           "--tags", "solution=fnol-delegated-v2", f"environmentId={state['environmentId']}")
    state["hostingLocation"] = args.hosting_location or state.get("hostingLocation", state["location"])
    state["hostingSku"] = args.hosting_sku or state.get("hostingSku", "B1")
    state["searchLocation"] = args.search_location or state.get("searchLocation", state["location"])
    save(state)
    network_parameters = STATE.parent / "network.parameters.json"
    network_parameters.write_text(json.dumps({
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"vaultAllowedIpv4Cidrs": {"value": (
            state["vaultNetwork"]["cidrs"]
            if state.get("vaultNetwork", {}).get("status") == "verified" else []
        )}},
    }), encoding="utf-8")
    deployment = az(
        "deployment", "group", "create", "--resource-group", name,
        "--name", "v2-foundation", "--template-file", str(ROOT / "v2" / "infra.bicep"),
        "--parameters", f"location={state['location']}", f"prefix={state['prefix']}",
        f"environmentId={state['environmentId']}", f"operatorId={state['operatorId']}",
        f"hostingLocation={state['hostingLocation']}", f"hostingSku={state['hostingSku']}",
        f"searchLocation={state['searchLocation']}",
        "@" + str(network_parameters),
    )
    state["azure"] = {key: value["value"] for key, value in deployment["properties"]["outputs"].items()}
    save(state)
    print("Isolated Azure foundation deployed.")


def private_state_network(state):
    group = az("group", "show", "--name", state["resourceGroup"])
    if group.get("tags", {}).get("environmentId") != state["environmentId"]:
        raise ValueError("The resource group does not belong to this v2 environment.")
    for kind, name in (("storage", state["prefix"] + "state"), ("webapp", state["azure"]["webName"])):
        arguments = ("storage", "account", "show") if kind == "storage" else ("webapp", "show")
        resource = az(*arguments, "--resource-group", state["resourceGroup"], "--name", name)
        if resource.get("tags", {}).get("environmentId") != state["environmentId"]:
            raise ValueError("Private networking is restricted to the owned v2 resources.")
    deployment = az(
        "deployment", "group", "create", "--resource-group", state["resourceGroup"],
        "--name", "v2-private-state-network", "--template-file", str(ROOT / "v2" / "network.bicep"),
        "--parameters", f"prefix={state['prefix']}", f"environmentId={state['environmentId']}",
        f"location={state['hostingLocation']}", f"webName={state['azure']['webName']}",
    )
    state["privateNetwork"] = {
        key: value["value"] for key, value in deployment["properties"]["outputs"].items()
    }
    save(state)
    print("Private Blob endpoint, private DNS and web VNet integration deployed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=(
        "init", "foundation", "vault-network", "identity", "publish", "verify-publish",
        "fabric-data", "knowledge", "ontology", "agent", "workiq", "cases", "private-network", "notifications",
        "billing-audience",
        "teams", "teams-publish", "email-source", "email-intake", "investigation-instructions",
        "email-enable", "email-folder", "mail-identity", "governance",
        "email-screening-agent", "governance-ownership",
    ))
    parser.add_argument("--tenant")
    parser.add_argument("--subscription")
    parser.add_argument("--prefix")
    parser.add_argument("--location", default="eastus2")
    parser.add_argument("--hosting-location")
    parser.add_argument("--hosting-sku", choices=("B1", "P0v3", "P1v3"))
    parser.add_argument("--search-location")
    parser.add_argument("--approve-selected-public-access", action="store_true")
    parser.add_argument("--vault-name")
    parser.add_argument("--approve-definition-sha256")
    args = parser.parse_args()
    if args.stage == "init":
        if not all((args.tenant, args.subscription, args.prefix)):
            parser.error("init requires --tenant, --subscription and --prefix")
        init(args)
    elif args.stage == "foundation":
        foundation(load(), args)
    elif args.stage == "private-network":
        private_state_network(load())
    elif args.stage == "notifications":
        from v2.notifications import configure_notifications
        configure_notifications(load())
    elif args.stage == "email-source":
        from v2.mail_source import configure_source
        configure_source(load())
    elif args.stage == "email-intake":
        from v2.email_deploy import provision_email
        provision_email(load())
    elif args.stage == "investigation-instructions":
        from v2.agents import update_instructions
        update_instructions(load())
    elif args.stage == "email-enable":
        from v2.email_enable import enable_email
        enable_email(load())
    elif args.stage == "mail-identity":
        from v2.mail_identity import configure_mail_identity
        configure_mail_identity(load())
    elif args.stage == "governance":
        from v2.governance import inventory
        inventory(load())
    elif args.stage == "governance-ownership":
        from v2.governance import configure_ownership
        configure_ownership(load())
    elif args.stage == "email-folder":
        from v2.email_deploy import rebind_email_folder
        rebind_email_folder(load())
    elif args.stage == "email-screening-agent":
        from v2.email_deploy import update_screening_agent
        update_screening_agent(load())
    elif args.stage == "billing-audience":
        from v2.workiq import prepare_billing_audience
        prepare_billing_audience(load())
    elif args.stage in ("teams", "teams-publish"):
        from v2.teams import configure_teams, publish_teams
        (configure_teams if args.stage == "teams" else publish_teams)(load())
    elif args.stage in ("fabric-data", "knowledge", "ontology", "agent", "workiq", "cases"):
        state = load()
        if args.stage == "cases":
            from v2.data import seed_private_cases
            seed_private_cases(state)
            return
        if args.stage == "workiq":
            from v2.workiq import provision_workiq
            provision_workiq(state)
            return
        if args.stage == "agent":
            from v2.agents import provision_agent
            provision_agent(state)
            return
        if args.stage == "ontology":
            from v2.ontology import create_approved_ontology
            create_approved_ontology(state, args.approve_definition_sha256)
            return
        if args.stage == "knowledge":
            from v2.knowledge import provision_knowledge
            provision_knowledge(state)
            return
        from v2.data import provision_fabric_data
        if not state.get("fabricCapacity"):
            raise RuntimeError("Resolve and explicitly approve a Fabric capacity first.")
        provision_fabric_data(state)
    else:
        from v2.provision import (
            configure_vault_network, ensure_web_identity, publish_app, verify_published_app,
        )
        state = load()
        if "azure" not in state:
            raise RuntimeError("The Azure foundation must finish before this stage.")
        if args.stage == "vault-network":
            configure_vault_network(state, approved=args.approve_selected_public_access)
        elif args.stage == "identity":
            ensure_web_identity(state, vault_name=args.vault_name)
        elif args.stage == "verify-publish":
            verify_published_app(state)
        else:
            publish_app(state)


if __name__ == "__main__":
    main()
