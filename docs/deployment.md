# FNOL v2 deployment guide (no Copilot required)

The deployment script is **`v2\deploy.py`**, run as
**`python -m v2.deploy <stage>` from the repository root**. There is no all-in-one
`deploy_solution.ps1` in this v2 revision. Do not run the archived v1 installer.

This is a staged, administrator-assisted pilot deployment, not a one-click
production installer. A person must select the tenant, capacity, accounts and
approved scopes; complete consent; approve the Teams app; and verify real user
access. Copilot, Copilot CLI, VS Code and automated test tools are not needed.

## 1. Supported scope and important limits

- Deploy into a **new, isolated** resource group and Fabric workspace.
- Use synthetic data, illustrative knowledge and an explicitly approved pilot
  mailbox, non-Inbox folder and subject tag.
- The attended interface is a personal Teams tab. Do not publish a second native
  Foundry Teams bot or replace user delegation with an agent/service account.
- Blob application state has a private endpoint and public access is disabled.
  This does **not** mean every service in the solution is private-only: the
  application, Foundry and Search use separately governed network settings.
- No v1 repository or v1 cloud workflow is required. New initialization records
  `protectedWorkflows: []`, allowing standalone email and governance stages.
  Optional fingerprints protect explicitly selected existing workflows. Older
  pilot `emailIntake.protectedV1Workflows` entries are still enforced, including
  when the new list is empty; do not remove or empty them to bypass protection.
- Agent 365 onboarding and restricted-user ontology initialization are parked.
  Their completion is not a prerequisite for an owner-path pilot demo, but they
  must not be reported as accepted or production-ready.

The current pilot's legacy fingerprints are compatibility safeguards, not a
requirement to create or retain v1 resources in a new environment. Fresh-tenant
end-to-end acceptance must still be performed; local checks are not that evidence.

## 2. Prerequisites

### Local tools

| Requirement | Details |
|---|---|
| Windows PowerShell 5.1 or PowerShell 7 | Commands below use Windows paths. |
| Python 3.12 | Install from [python.org](https://www.python.org/downloads/windows/); enable access through `python` or `py`. |
| Git | Required when acquiring a published revision from GitHub. |
| Azure CLI | Install using [Microsoft's Windows instructions](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows). |
| Bicep CLI | Install through `az bicep install`. |
| Microsoft ODBC Driver 18 for SQL Server | Required for the Fabric SQL readback; use [Microsoft's installer](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server). |
| Network access | Approved access to Azure management, Entra, Graph, Foundry, Search, Fabric/OneLake, the Fabric SQL endpoint, App Service SCM and the selected Key Vault. |

Node.js is not required for deployment. Gunicorn is installed on the Linux
application host; its conditional dependency is not installed on Windows.

### Tenant, licenses, capacity and permissions

Obtain these approvals before running any provisioning command:

| Area | Required approval/access |
|---|---|
| Azure | Selected subscription, provider registration, resource creation and role-assignment rights. Subscription Owner can provision the foundation; Contributor alone cannot create its RBAC assignments. Activate privileged access only for the operations requiring it. |
| Regions and quota | Approved Foundry, Search and Linux App Service regions; available service quota and globally unique names. The template requests `gpt-5-mini`, version `2025-08-07`, deployment `investigation`, `GlobalStandard` capacity **30**. Verify availability before running `foundation`. |
| Entra/Graph administration | Authority to create the two owned single-tenant apps, configure exposed scopes, manage the exact delegated consents, and create the approved Team/group. A directory role does not substitute for the Graph delegated scopes in the actual CLI token. Stop on a permission/consent failure; obtain approved administrator access rather than adding arbitrary broad grants. |
| Fabric | Active approved capacity, enabled Fabric/ontology functionality in a supported region, rights to create a workspace/lakehouse/ontology and read the data through SQL/OneLake. Each invoking user needs independently approved item/source access and the applicable Fabric license. |
| Foundry user access | Each human needs project invocation access and any resource-read access required by the documented enterprise RBAC pattern. Foundry User/Project Manager roles were formerly named Azure AI User/Project Manager; verify role IDs and actual scopes rather than guessing from the displayed name. |
| Work IQ | One-time tenant service-principal setup and exact `WorkIQAgent.Ask` consent by the approved administrator. Current official MCP prerequisites require **Copilot Credits usage-based billing** and an appropriate spending policy. Connector-backed tools can have separate Microsoft 365/Copilot license requirements. A Copilot license does not replace MCP billing. |
| Outlook/Teams | Approved pilot mailbox and sender, permissions to manage its folder/rule through the separately authenticated Office 365 connector, Teams licensing and a Teams administrator who can approve and restrict the custom app to pilot users. |
| Key Vault | An RBAC vault reachable from the deployer's approved network and the application. Use either the owned vault with explicitly approved selected-IP access, or an explicitly approved existing vault. Do not bypass tenant policy. |

Work IQ provisioning creates a dedicated OAuth client and an **AllPrincipals
delegated** grant for the exact Work IQ scope. It is not application-only
permission and does not replace each person's connection sign-in or source ACLs.
This tenant consent and billing boundary require explicit approval.

## 3. Acquire the v2 code and create the Python environment

For the maintainer's already supplied checkout:

```powershell
Set-Location 'C:\Sagar\MicrosoftIQ\Ins_FNOL_MicrosoftIQ_Logicapps_accelerator_v2'
```

That checkout already contains the running pilot's private state. **Do not run
`init` there for another deployment or delete its state.** For a separate clean
deployment from the supplied local source, copy only core files:

```powershell
$sourceRoot = (Get-Location).Path
$deploymentRoot = '<NEW_LOCAL_DEPLOYMENT_FOLDER>'
if (Test-Path -LiteralPath $deploymentRoot) {
    throw 'Use a new deployment folder; do not overwrite an existing checkout.'
}
New-Item -ItemType Directory -Path $deploymentRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'v2') -Destination $deploymentRoot -Recurse
foreach ($name in @('README.md', 'requirements.txt', '.gitignore')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination $deploymentRoot
}
$docsRoot = Join-Path $deploymentRoot 'docs'
New-Item -ItemType Directory -Path $docsRoot | Out-Null
foreach ($name in @('deployment.md', 'demo.md')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot "docs\$name") -Destination $docsRoot
}
Set-Location $deploymentRoot
```

Do not copy `.secrets`, `.git`, `.venv`, `temp` or saved cloud IDs. The local Word
email pack is not a deployment dependency; if using it for a new demo, replace
its recipient with the newly approved mailbox. Follow the remaining stages from
this clean folder with a new unique prefix and fresh tenant-specific approvals.

Other users must obtain the **v2 branch** or the supplied v2 source package.
The GitHub default branch `main` intentionally retains v1; cloning the default
branch does not obtain this solution.

To obtain the v2 branch:

```powershell
git clone --branch 'sagarbathe-microsoft-delegated-agent-authentication' `
  'https://github.com/sagarbathe/Ins_FNOL_MicrosoftIQ_Logicapps_accelerator.git' `
  'C:\Work\FNOL-v2'
Set-Location 'C:\Work\FNOL-v2'
```

Replace every `<...>` placeholder in this guide with an approved real value.
Do not paste literal placeholders into an actual deployment.

```powershell
py -3.12 -m venv .venv
# Activation is optional. The explicit interpreter avoids execution-policy changes.
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

az version
az bicep install
& $python -c "import pyodbc; print(pyodbc.drivers())"
& $python -m v2.deploy --help
```

Confirm the ODBC output contains **ODBC Driver 18 for SQL Server**. Stop and
install the official driver if absent.

Use the same repository root and interpreter for all subsequent commands.
In a new terminal, restore `$python`. Do not run deployment stages concurrently.

## 4. Sign in and initialize once

**Tool/service:** PowerShell and Azure CLI; approved native deployment operator.

```powershell
$tenantId = '<TENANT_GUID>'
$subscriptionId = '<SUBSCRIPTION_GUID>'
$prefix = '<NEW_UNIQUE_PREFIX>'
$location = '<APPROVED_FOUNDRY_REGION>'
$hostingLocation = '<APPROVED_APP_SERVICE_REGION>'
$searchLocation = '<APPROVED_SEARCH_REGION>'

az login --tenant $tenantId
if ($LASTEXITCODE -ne 0) { throw 'Azure sign-in failed.' }
az account set --subscription $subscriptionId
az account show --query '{tenantId:tenantId,subscription:id,user:user.name}'
az ad signed-in-user show --query '{id:id,userPrincipalName:userPrincipalName}'
```

Verify the native operator, tenant and subscription. The prefix must contain
6-16 lowercase alphanumeric characters, starting with a letter.

**New deployment only:**

```powershell
& $python -m v2.deploy init --tenant $tenantId --subscription $subscriptionId `
  --prefix $prefix --location $location
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
```

This creates `.secrets\v2\deployment.json` and an ownership UUID. It refuses an
existing state file or existing `rg-<prefix>-v2` resource group.
It also records an explicit empty `protectedWorkflows` list. No legacy-workflow
IDs or fingerprints are needed for a standalone environment.

**Existing pilot:** Do not run `init`, delete its state or replace it with
another environment's JSON. Keep a protected backup and resume only the intended
stage. Each stage verifies the CLI tenant/subscription against the saved state.

Keep `.secrets` restricted to the operator and administrators using Windows
folder **Properties > Security > Advanced**. Never put state, tokens, generated
packages or exports in Git.

For manual configuration in later sections, define these helpers:

```powershell
$statePath = Join-Path (Get-Location) '.secrets\v2\deployment.json'
function Read-FnolState {
    Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
}
function Set-FnolField([string]$name, $value) {
    $s = Read-FnolState
    $s | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force
    $json = $s | ConvertTo-Json -Depth 100
    $temporary = [IO.Path]::ChangeExtension($statePath, '.tmp')
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}
```

The UTF-8-without-BOM write is intentional for Windows PowerShell 5.1.
Only edit documented operator-input fields while no stage is running; preserve
all saved resource IDs, operation IDs, digests and runtime evidence.

## 5. Deploy Azure foundation and private state networking

```powershell
& $python -m v2.deploy foundation --hosting-location $hostingLocation `
  --hosting-sku B1 --search-location $searchLocation
if ($LASTEXITCODE -ne 0) { throw 'Foundation failed; inspect Azure deployment operations.' }

& $python -m v2.deploy private-network
if ($LASTEXITCODE -ne 0) { throw 'Private state networking failed.' }
```

**Expected:** Owned App Service, Foundry account/project/model, Search, state
storage, Key Vault and scoped identities/RBAC are recorded. The second stage
creates the Blob private endpoint, private DNS and outbound web VNet integration.

The template supports hosting SKUs `B1`, `P0v3` and `P1v3`. Select an approved
available SKU rather than editing quotas or regions blindly.

## 6. Configure the vault and owned web identity

Choose **one** approved vault path:

**Owned vault, selected public IPv4 access explicitly permitted:**

```powershell
& $python -m v2.deploy vault-network --approve-selected-public-access
if ($LASTEXITCODE -ne 0) { throw 'Approved vault network setup failed.' }
& $python -m v2.deploy identity
if ($LASTEXITCODE -ne 0) { throw 'Web identity setup failed.' }
```

This is selected-IP access with deny-by-default rules, not public access for
everyone. Do not run the approval flag without the network owner's decision.

**Approved existing RBAC vault:**

```powershell
& $python -m v2.deploy identity --vault-name '<APPROVED_EXISTING_RBAC_VAULT>'
if ($LASTEXITCODE -ne 0) { throw 'Existing-vault identity setup failed.' }
```

Have the vault owner verify tenant/subscription, RBAC and reachable network
paths first. This creates namespaced v2 secrets and secret-scoped assignments;
it does not relax the existing vault firewall or tenant policy.

**Expected:** One owned single-tenant web application, a managed identity and
namespaced startup secrets. The managed identity can operate infrastructure,
but cannot stand in for a human's IQ retrieval.

## 7. Publish and verify the application

```powershell
& $python -m v2.deploy publish
if ($LASTEXITCODE -ne 0) { throw 'Application publication failed.' }
& $python -m v2.deploy verify-publish
if ($LASTEXITCODE -ne 0) { throw 'Live runtime verification failed.' }
```

The runtime package excludes deployment tools, synthetic fixtures, tests,
diagnostics and local secrets. Verification compares the exact runtime build,
process health and rejection of anonymous case access.

Do not interpret health as end-to-end delegated acceptance.

## 8. Select capacity and load Fabric data

**Tool/service:** Fabric admin/capacity settings and PowerShell.

Select an approved **Active** capacity in the correct tenant/region. An
authorized operator can inspect available capacities with:

```powershell
az rest --method get --url 'https://api.fabric.microsoft.com/v1/capacities' `
  --resource 'https://api.fabric.microsoft.com'
```

Confirm its actual GUID, then:

```powershell
Set-FnolField 'fabricCapacity' @{ id = '<APPROVED_ACTIVE_CAPACITY_GUID>' }
& $python -m v2.deploy fabric-data
if ($LASTEXITCODE -ne 0) { throw 'Fabric data setup or verification failed.' }
```

This generates local CSV/Parquet under `.secrets\v2\synthetic`, creates the owned
workspace and lakehouse, loads nine tables using the Fabric Load Table API and
verifies exact counts through the SQL analytics endpoint.

**Expected:** The saved `fabric.expectedCounts` and `fabric.verifiedCounts`
match. An ontology definition preview and its SHA-256 are saved under
`.secrets\v2`. No Fabric notebook, recurring pipeline or Power BI model is
created. SQL endpoint synchronization can take time; a timeout is not permission
to recreate completed tables.

## 9. Review and create only the Gen 2 ontology

Inspect `.secrets\v2\ontology.definition.json` and its generated TMDL bindings.
Confirm the new workspace/lakehouse IDs, nine tables and eight entity types.

```powershell
$s = Read-FnolState
$definitionHash = $s.fabric.ontologyPreview.sha256
if (-not $definitionHash) { throw 'No verified ontology preview hash exists.' }
Write-Output "Reviewed definition SHA-256: $definitionHash"
# Run only after a reviewer approves that exact unchanged definition.
& $python -m v2.deploy ontology --approve-definition-sha256 $definitionHash
if ($LASTEXITCODE -ne 0) { throw 'Gen 2 ontology creation/readback failed.' }
```

The service must report generation **2**, and returned TMDL bindings must match.
The `/v1/` REST path is the API version, not proof of a Gen 1 ontology.
No legacy Data Agent/OpenAPI fallback is allowed.

In Fabric, service-created ontology companion items can include a GraphModel,
Eventhouse and KQLDatabase. They are not omitted notebook deployments.

## 10. Create ACL-protected Foundry IQ knowledge

```powershell
& $python -m v2.deploy knowledge
if ($LASTEXITCODE -ne 0) { throw 'Knowledge setup or ACL readback failed.' }
```

This indexes the five illustrative documents in `v2\knowledge_docs`, creates
the native knowledge-base connection and verifies permitted/control retrieval.
Default document grants are explicit operator IDs, not wildcard access.

For other users, have the knowledge owner separately approve document grants
before editing `knowledgeGrants.documents` and rerunning the stage. Do not
broaden grants to make a failed demo succeed.

## 11. Optional Work IQ setup before pinning the agent

For the full three-IQ demo, the approved Microsoft 365 administrator first
completes the official Work IQ tenant service-principal setup and verifies
Copilot Credits billing/spending coverage. Billing is a separate admin-center
operation; `billing-audience` does not create or enable a spending policy.

After the administrator's one-time setup, resolve the existing official resource:

```powershell
az ad sp list --filter "appId eq 'fdcc1f02-fc51-4226-8753-f668596af7f7'" `
  --query '[].{objectId:id,applicationId:appId,displayName:displayName}'
```

Confirm a unique tenant service principal. Record its **object ID**, not the
application ID, and the explicit approval:

```powershell
Set-FnolField 'workiq' @{
    enablementApproved = $true
    resourcePrincipalId = '<TENANT_WORK_IQ_SERVICE_PRINCIPAL_OBJECT_GUID>'
}
& $python -m v2.deploy billing-audience
if ($LASTEXITCODE -ne 0) { throw 'Approved billing audience setup failed.' }
& $python -m v2.deploy workiq
if ($LASTEXITCODE -ne 0) { throw 'Work IQ connection setup failed.' }
```

Do not replace an existing `workiq` field during maintenance: it also contains
the existing owned OAuth identity, connection and consent evidence.

The created billing audience contains the operator only. An existing all-user
spending policy is not pilot-only merely because this group exists.

The `workiq` stage creates the dedicated OAuth client, exact delegated consent,
managed connection and trusted callback registration. Each human must still
authorize the connection when prompted. This is not application-only Work IQ.

If Work IQ is not approved, omit this section. The agent explicitly reports
Work IQ as unavailable; do not claim a three-IQ acceptance result.

## 12. Pin the read-only investigation agent and seed cases

```powershell
& $python -m v2.deploy agent
if ($LASTEXITCODE -ne 0) { throw 'Native delegated agent configuration failed.' }
& $python -m v2.deploy cases
if ($LASTEXITCODE -ne 0) { throw 'Encrypted private case bootstrap failed.' }
```

The agent discovers the actual native ontology MCP endpoint and audience,
configures `UserEntraToken` with the documented ontology delegated scopes,
restricts allowed tools and pins an exact version.

The case stage runs an Entra-authenticated bootstrap inside the private-connected
application container. It creates three encrypted operator-assigned synthetic
cases and an unassigned denial control. It does not expose a public seeding API.

An administrator must independently verify each human's Foundry invocation role,
Fabric item/source permissions and required delegated consent using the official
setup references below. Case assignment, Teams installation, OAuth consent and
source permission are four different gates.

## 13. Configure, approve and install the personal Teams app

```powershell
& $python -m v2.deploy teams
if ($LASTEXITCODE -ne 0) { throw 'Teams SSO/package setup failed.' }
& $python -m v2.deploy teams-publish
if ($LASTEXITCODE -ne 0) { throw 'Catalog submission failed; use the documented admin upload path.' }
```

`teams` exposes `access_as_user` on the owned web registration, preauthorizes
the documented Teams clients and builds `.secrets\v2\teams-app.zip`.
`teams-publish` submits it for review; it does not approve/install it or change
app availability policies.

**Teams administrator, Teams admin center:**

1. Under **Teams apps > Manage apps**, locate the submitted **FNOL
   Investigation** app, or upload the generated ZIP if approved Graph submission
   is unavailable.
2. Verify the package's environment-specific manifest ID and exact uploaded
   package; do not adopt an unrelated existing catalog app.
3. Approve/allow the app and restrict **Users and groups** availability to the
   explicitly approved pilot users.
4. Install the personal app for those users using the tenant's supported
   installation controls, or have them open **Apps > Built for your org**.
5. Verify actual installation and case opening with the real Teams profile.

After Graph submission, the catalog ID is saved automatically. If manual upload
was necessary, record its exact catalog ID and uploaded package/content digests
only after admin readback; do not rerun submission to adopt an untracked app.
The following records an operator-verified manual upload:

```powershell
$s = Read-FnolState
$t = $s.teams
$t | Add-Member -NotePropertyName catalogId -NotePropertyValue '<VERIFIED_CATALOG_GUID>' -Force
$packageHash = (Get-FileHash '.secrets\v2\teams-app.zip' -Algorithm SHA256).Hash.ToLowerInvariant()
$contentHash = & $python -c "from pathlib import Path; from v2.teams import package_content_sha256; print(package_content_sha256((Path('.secrets') / 'v2' / 'teams-app.zip').read_bytes()))"
if ($LASTEXITCODE -ne 0) { throw 'Package content hashing failed.' }
$t | Add-Member -NotePropertyName publishedSha256 -NotePropertyValue $packageHash -Force
$t | Add-Member -NotePropertyName publishedContentSha256 -NotePropertyValue $contentHash -Force
Set-FnolField 'teams' $t
```

Record pilot IDs and availability **only after** the administrator verifies
those actual settings. For an operator-only pilot:

```powershell
$s = Read-FnolState
$t = $s.teams
$t | Add-Member -NotePropertyName pilotUserIds -NotePropertyValue @($s.operatorId) -Force
$t | Add-Member -NotePropertyName pilotAvailabilityVerified -NotePropertyValue $true -Force
Set-FnolField 'teams' $t
```

Additional approved users require their literal object IDs, actual app
availability/installation, notification Team membership where needed and
independent source permissions. Do not mark verification flags merely to
bypass a deployment gate.

## 14. Configure private Teams notifications

```powershell
& $python -m v2.deploy notifications
if ($LASTEXITCODE -ne 0) { throw 'Notification destination setup failed.' }
```

This creates/verifies the owned private pilot Team, **Synthetic intake** channel
and dedicated Teams API connection. It does not deploy a synthetic HTTP-trigger
workflow. Add only approved pilot members through Teams.

**Azure portal:** Open the owned `<prefix>-notify-teams` API connection, select
its edit/authorization action and authenticate as the approved native operator.
The exact resource ID is saved in `notifications.connectionId`; use that if the
display name differs.

Rerun `notifications` after authorization. The script checks
`properties.authenticatedUser.name` and Connected status; cosmetic `displayName`
is not evidence of the authorizing account.

## 15. Approve mailbox isolation and create its dedicated connector

Standalone deployment does not require any existing workflow. Keep the explicit
empty `protectedWorkflows` list created by `init` unless other workflows need
protection. Mailbox isolation approval remains mandatory.

The mailbox owner approves the exact mailbox, new/empty non-Inbox root folder,
bounded subject tag and effects on existing inbox rules. For new setup:

```powershell
Set-FnolField 'emailIntake' @{
    sourceIsolationApproved = $true
    mailbox = '<APPROVED_PILOT_MAILBOX_UPN>'
    folderName = '<APPROVED_NEW_EMPTY_ROOT_FOLDER>'
    subjectTag = '[FNOL-V2]'
}
```

Do not replace the whole field during maintenance; preserve existing source IDs,
workflow state, hashes, assignment and acceptance/recovery evidence.

**Optional, only when existing workflows need protection:** capture their actual
definition/parameters/state fingerprints without changing them. Otherwise skip
the following block; do not create dummy workflows or fingerprints.

```powershell
$protectedWorkflowIds = @('<APPROVED_PROTECTED_WORKFLOW_ARM_ID>')
@'
import hashlib, json, sys
from v2.deploy import load, save
from v2.provision import clients
s = load()
arm = clients(s)[1]
protected = []
for resource_id in sys.argv[1:]:
    p = arm.call("GET", resource_id + "?api-version=2019-05-01")["properties"]
    snapshot = {k: p.get(k) for k in ("definition", "parameters", "state")}
    protected.append({"id": resource_id, "sha256": hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode()).hexdigest()})
if not protected:
    raise ValueError("For optional protection, supply approved real workflow IDs; standalone init already records an empty list.")
s["protectedWorkflows"] = protected
save(s)
'@ | & $python - $protectedWorkflowIds
if ($LASTEXITCODE -ne 0) { throw 'Protected workflow snapshot failed.' }
```

Never refresh fingerprints to conceal a changed protected workflow.
During maintenance, do not replace an existing protection list without reviewing
all previously protected resources. Existing `emailIntake.protectedV1Workflows`
entries remain mandatory and are checked in addition to this generic list. An
empty generic list does not disable those older pilot safeguards.

Create only the separately owned Office 365 connection:

```powershell
@'
from v2.deploy import load, save
from v2.provision import clients
s = load()
arm = clients(s)[1]
path = (f"/subscriptions/{s['subscriptionId']}/resourceGroups/{s['resourceGroup']}"
        f"/providers/Microsoft.Web/connections/{s['prefix']}-email-intake")
old = arm.call("GET", path + "?api-version=2016-06-01", missing_ok=True)
if old and old.get("tags", {}).get("environmentId") != s["environmentId"]:
    raise ValueError("Existing connection is unowned; it was not overwritten.")
if not old:
    api = (f"/subscriptions/{s['subscriptionId']}/providers/Microsoft.Web"
           f"/locations/{s['location']}/managedApis/office365")
    arm.call("PUT", path + "?api-version=2016-06-01", {
        "location": s["location"], "tags": {"environmentId": s["environmentId"]},
        "properties": {"displayName": s["prefix"] + " isolated mailbox", "api": {"id": api}}
    })
s["emailIntake"]["connectionId"] = path
save(s)
'@ | & $python -
if ($LASTEXITCODE -ne 0) { throw 'Dedicated mail connector setup failed.' }
```

**Azure portal:** Open that `<prefix>-email-intake` **API connection**, select
its edit/authorization action and sign in as the approved mailbox. The connection
and the later Logic App have the same suffix but different resource types.
Do not authorize it as the human investigator or reuse a v1 mail connection.

## 16. Configure source and deploy the watcher disabled

```powershell
& $python -m v2.deploy email-source
if ($LASTEXITCODE -ne 0) { throw 'Approved mailbox source setup failed.' }
& $python -m v2.deploy email-intake
if ($LASTEXITCODE -ne 0) { throw 'Disabled private email workflow setup failed.' }
& $python -m v2.deploy verify-publish
if ($LASTEXITCODE -ne 0) { throw 'Email-capable runtime verification failed.' }
```

The source stage verifies the connector's real mailbox, creates/adopts only the
approved empty folder and creates the exact inbox rule **disabled**. The watcher
uses the connector's opaque picker folder ID, not a guessed Graph folder ID.

The intake stage verifies pilot-only Teams availability, creates the tool-free
screening agent, deploys the watcher **disabled**, assigns only approved users to
incoming cases, configures infrastructure recovery permissions and verifies
any configured protected workflows are unchanged. With the fresh empty list,
there are no legacy workflow lookups; connector ownership, mailbox isolation,
pilot assignments and explicit enablement checks remain required.

This is a Consumption Logic App using ordinary connector/HTTP actions. It does
not claim to use the native preview Agent action.

## 17. Optional delegated mail escalation

```powershell
& $python -m v2.deploy mail-identity
if ($LASTEXITCODE -ne 0) { throw 'Pilot delegated Mail.Send consent verification failed.' }
```

This adds Graph delegated `Mail.Send` only for the named `teams.pilotUserIds`.
It does not send mail or create tenant-wide mail consent. The agent remains
read-only; the personal app requires an immutable preview and separate human
confirmation for an actual send.

## 18. Explicitly enable the approved route

Run only after source, package, installation, runtime and pilot assignments
have been verified and the mailbox owner approves activation:

```powershell
& $python -m v2.deploy email-enable
if ($LASTEXITCODE -ne 0) { throw 'Route activation failed; inspect partial state before testing.' }
```

This checks the exact live runtime, owned watcher definition/parameters,
mailbox/folder, authenticated connections, destination and unchanged routing
rule before enabling the watcher and exact rule. It records verified enablement.
There is no automatic synthetic acceptance-mail send.

If activation partially fails, inspect the saved state, actual watcher and rule.
Do not assume both remained disabled or blindly repeat a send.

## 19. Validate with actual users

Run [the demo runbook](demo.md) starting with **one** approved synthetic email.
Complete and record these checks locally:

| Gate | Required observation |
|---|---|
| Intake | Exact tagged message is received in the approved folder; original workflow run creates one private case and minimal card. |
| Teams | Actual pilot profile opens the card's exact case in the personal app. Unassigned access is denied. |
| Full source | Private screening and complete email text, including the demo end marker, render correctly. |
| Fabric/Foundry | Known-record and compound questions return real source citations, or clearly identified source failures. |
| Work IQ, if enabled | Same human completes connection authorization and receives permitted evidence or an explicit unavailable result. |
| Privacy | Another approved user does not see the first user's conversation/report; no private answers appear in the shared channel. |
| Escalation, if approved | Preview does not send; one exact confirmation has an audited outcome. HTTP 202 is not proof of delivery. |
| Recovery | Original run/card evidence is reconciled before any explicit approved retry; uncertainty never triggers an automatic resend. |

Do not set global readiness true based solely on deployment commands, unit tests
or an operator-only demo. Do not reopen parked restricted-user/Agent 365 work
without a new decision.

## Maintenance and troubleshooting

- `verify-publish` is the non-redeploy runtime check.
- `investigation-instructions` pins a reviewed instruction update without
  changing source connections.
- `email-screening-agent` updates the pinned tool-free screening instructions.
  If `TRIAGE_INSTRUCTIONS` in `v2\email_processing.py` was edited, follow this
  stage with `publish` and `verify-publish` so the serving runtime matches the
  reviewed code. Existing successful cases retain their original screening;
  validate the new pin using a fresh approved sample, not an automatic replay.
- `email-folder` is controlled folder rebinding for an existing verified
  watcher, not permission to adopt another mailbox/folder.
- `governance` is inventory, not Agent 365 onboarding. `governance-ownership`
  changes administrative ownership; neither is required for this parked gate.
- The removed `intake`, `email-test` and `email-reconcile-probe` commands are
  not part of the published installer.
- A quota/region/policy failure needs an approved alternative or corrected
  prerequisite. Do not keep repeating an identical failing stage.
- Key Vault firewall 403 is not resolved by widening RBAC. Check the network
  path and approved vault selection separately.
- A wrong connector account needs reauthorization; renaming it does nothing.
- For a Teams/Foundry consent failure, verify actual token scope/tenant,
  application registration, role assignments and installed-app availability.
  Never print tokens or use a shared account to bypass the failure.
- For an uncertain notification or send, preserve the original run/attempt and
  use the explicit private recovery controls documented in the demo.

## Official platform references

The commands above describe this repository's implementation. Platform
prerequisites and identity behavior are grounded in official documentation:

- [Foundry user delegation and supported ontology scopes](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/configure-user-delegation)
- [Native Fabric IQ integration and endpoint discovery](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq)
- [Ontology creation](https://learn.microsoft.com/en-us/rest/api/fabric/ontology/items/create-ontology)
- [Gen 2 ontology definitions](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition)
- [Lakehouse Load Table API](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/tables/load-table)
- [Foundry project RBAC](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
- [Native Search delegated-user ACL enforcement](https://learn.microsoft.com/en-us/azure/search/search-query-access-control-rbac-enforcement)
- [Work IQ MCP prerequisites and setup](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/work-iq)
- [Work IQ tenant enablement](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/enable-work-iq)
- [Teams tab SSO registration](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/authentication/tab-sso-register-aad)
- [Teams SSO code](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/authentication/tab-sso-code)
- [Microsoft identity OBO](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Teams catalog publication](https://learn.microsoft.com/en-us/graph/api/teamsapp-publish?view=graph-rest-1.0)
- [Office 365 Outlook connector](https://learn.microsoft.com/en-us/connectors/office365/)
- [Teams connector](https://learn.microsoft.com/en-us/connectors/teams/)
- [App Service VNet integration](https://learn.microsoft.com/en-us/azure/app-service/overview-vnet-integration)
- [Storage private endpoints](https://learn.microsoft.com/en-us/azure/storage/common/storage-private-endpoints)
