<#
.SYNOPSIS
    End-to-end deployment orchestrator for the Auto FNOL Triage accelerator
    (Agent Identity edition). Runs every step in
    docs/End_to_End_Deployment_Guide.docx Section 6, in order.

.DESCRIPTION
    This script automates everything that can be automated (Azure resource
    creation, app registration, role assignments, code deploy, config push)
    and pauses at the points that genuinely require a human (interactive
    device-code sign-in as the Agent Identity, filling in a couple of
    environment-specific values in .env / parameters.json / connections.json,
    and authorizing the Office 365 connector in the Azure Portal).

    Run it from the repo root. Each step is idempotent where possible - if a
    step already succeeded on a prior run (e.g. the user/app already exists),
    it will skip or warn rather than fail.

.PARAMETER SkipSteps
    Comma-separated list of step numbers to skip (e.g. "1,2" if the Agent
    Identity user and app registration already exist from a previous run).

.EXAMPLE
    .\deploy_solution.ps1
.EXAMPLE
    .\deploy_solution.ps1 -SkipSteps 1,2,3
#>
param(
    [string]$SkipSteps = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$Skip = @()
if ($SkipSteps) { $Skip = $SkipSteps.Split(",") | ForEach-Object { $_.Trim() } }

function Import-DotEnv($path) {
    if (-not (Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        Set-Item -Path "Env:$key" -Value $value
    }
}
Import-DotEnv "$RepoRoot\.env"

function Step-Header($num, $title) {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host " STEP $num - $title" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan
}

function Should-Skip($num) {
    if ($Skip -contains "$num") {
        Write-Host "Skipping step $num (requested via -SkipSteps)." -ForegroundColor Yellow
        return $true
    }
    return $false
}

function Confirm-Continue($message) {
    Write-Host $message -ForegroundColor Yellow
    $resp = Read-Host "Press Enter to continue once done (or type 'skip' to skip this step)"
    return ($resp -ne "skip")
}

# ---------------------------------------------------------------------
# Step 0: Prerequisites check
# ---------------------------------------------------------------------
Step-Header 0 "Prerequisites check"
if (-not (Should-Skip 0)) {
    az account show -o none
    if (-not $?) { throw "Not logged in to Azure CLI. Run 'az login' first. Docs: https://learn.microsoft.com/cli/azure/authenticate-azure-cli" }
    python --version
    if (-not $?) { throw "Python not found on PATH. Download: https://www.python.org/downloads/" }
    if (-not (Test-Path "$RepoRoot\.env")) {
        Write-Host "No .env found - copying from .env.example. You MUST fill in real values before continuing." -ForegroundColor Yellow
        Write-Host "Guidance for locating each value is in docs/End_to_End_Deployment_Guide.docx, Section 6.0 'Required .env values'." -ForegroundColor Cyan
        Copy-Item "$RepoRoot\.env.example" "$RepoRoot\.env"
        notepad "$RepoRoot\.env"
        Read-Host "Press Enter once .env is filled in"
    }
    Write-Host "Prerequisites OK." -ForegroundColor Green
}

# ---------------------------------------------------------------------
# Step 1: Create the Agent Identity user
# ---------------------------------------------------------------------
Step-Header 1 "Create the Agent Identity user"
if (-not (Should-Skip 1)) {
    $upn = Read-Host "Enter the Agent Identity's UPN (e.g. svc-fnol-agent@yourtenant.onmicrosoft.com)"
    $existing = az ad user show --id $upn -o none 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "User $upn already exists - skipping creation." -ForegroundColor Yellow
    } else {
        $pwd = Read-Host "Enter a strong temporary password for this user" -AsSecureString
        $pwdPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwd))
        az ad user create --display-name "FNOL Automation Agent" --user-principal-name $upn `
            --password $pwdPlain --force-change-password-next-sign-in false -o none
        Write-Host "Created user $upn." -ForegroundColor Green
    }
    Confirm-Continue @"
MANUAL STEPS required in the Azure/M365 portal before continuing:
  1. Assign this user an M365 license including Exchange Online + Teams.
     Go to: https://admin.microsoft.com/Adminportal/Home#/users
     -> click the user -> Licenses and Apps -> select a license that
     includes Exchange Online + Microsoft Teams -> Save changes.
  2. Add this user as a MEMBER of the target Teams team.
     Go to: https://admin.teams.microsoft.com/teams/manage-teams
     -> click the target team -> Members -> Add member -> search by UPN.
     (Alternatively, in the Teams desktop/web app: Teams -> ... next to the
     team name -> Add member.)
  3. Grant this user Contributor on the Fabric workspace hosting Fabric IQ.
     Go to: https://app.fabric.microsoft.com -> open the workspace ->
     Manage access (top right) -> Add people or groups -> search by UPN ->
     select role "Contributor" -> Add.
  4. Grant this user 'Cognitive Services User' role on the Foundry account.
     Go to: https://portal.azure.com -> search for the Foundry/Cognitive
     Services account (e.g. sbazureaimodels) -> Access control (IAM) ->
     Add -> Add role assignment -> select "Cognitive Services User" ->
     Members -> select this user -> Review + assign.

  See docs/End_to_End_Deployment_Guide.docx, Section 6.2 "Step 1" for
  full screenshots/detail and troubleshooting tips.
"@ | Out-Null
}

# ---------------------------------------------------------------------
# Step 2: Register the Agent Identity public-client app
# ---------------------------------------------------------------------
Step-Header 2 "Register the Agent Identity public-client app"
if (-not (Should-Skip 2)) {
    $appName = "FNOL-AgentIdentity-GraphPublicClient"
    $existingApp = az ad app list --display-name $appName --query "[0].appId" -o tsv
    if ($existingApp) {
        Write-Host "App '$appName' already exists (appId=$existingApp) - skipping creation." -ForegroundColor Yellow
        $appId = $existingApp
    } else {
        $appJson = az ad app create --display-name $appName --sign-in-audience AzureADMyOrg `
            --is-fallback-public-client true -o json | ConvertFrom-Json
        $appId = $appJson.appId
        Write-Host "Created app registration: $appId" -ForegroundColor Green

        # Microsoft Graph delegated scopes: Mail.Read, Sites.Read.All, Chat.Read, ChannelMessage.Send
        az ad app permission add --id $appId --api 00000003-0000-0000-c000-000000000000 --api-permissions `
            570282fd-fa5c-430d-a7fd-fc8dc98a9dca=Scope 205e70e5-aba6-4c52-a976-6d2d46c48043=Scope `
            f501c180-9344-439a-bca0-6cbf209fd270=Scope ebf0f66e-9fb1-49e4-a278-222f76911cf4=Scope -o none

        # Power BI / Fabric delegated scopes: Item.Read.All, Item.Execute.All
        az ad app permission add --id $appId --api 00000009-0000-0000-c000-000000000000 --api-permissions `
            d2bc95fc-440e-4b0e-bafd-97182de7aef5=Scope caf40b1a-f10e-4da1-86e4-5fda17eb2b07=Scope -o none

        # Azure SQL Database delegated scope: user_impersonation
        az ad app permission add --id $appId --api 022907d3-0f1b-48f7-badc-1ba6abab6d66 --api-permissions `
            c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9=Scope -o none

        Start-Sleep -Seconds 10  # let the permission adds propagate before consent
        az ad app permission admin-consent --id $appId -o none
        Write-Host "Requested admin consent for all 3 resources." -ForegroundColor Green
        Write-Host "If any resource's consent silently failed (a known CLI quirk for multi-resource apps)," -ForegroundColor Yellow
        Write-Host "verify with: az ad app permission list-grants --id $appId" -ForegroundColor Yellow
    }
    Write-Host "AGENT_IDENTITY_PUBLIC_CLIENT_ID = $appId" -ForegroundColor Cyan
    Write-Host "Add/verify this value in .env now." -ForegroundColor Yellow
    Write-Host "Where to find related values:" -ForegroundColor Cyan
    Write-Host "  - AGENT_IDENTITY_TENANT_ID: run 'az account show --query tenantId -o tsv'" -ForegroundColor Cyan
    Write-Host "  - AGENT_IDENTITY_UPN: the Agent Identity's UPN entered in Step 1" -ForegroundColor Cyan
    Write-Host "  - AGENT_IDENTITY_PUBLIC_CLIENT_ID: printed above, or Portal -> Microsoft Entra ID ->" -ForegroundColor Cyan
    Write-Host "    App registrations -> $appName -> Overview -> Application (client) ID" -ForegroundColor Cyan
    Write-Host "  - Verify all 3 consent grants succeeded: az ad app permission list-grants --id $appId" -ForegroundColor Cyan
    Write-Host "    (should show Graph + Power BI/Fabric + Azure SQL - if one is missing, grant it manually at" -ForegroundColor Cyan
    Write-Host "    Portal -> App registrations -> $appName -> API permissions -> Grant admin consent)" -ForegroundColor Cyan
    Write-Host "Docs: App registrations - https://learn.microsoft.com/entra/identity-platform/quickstart-register-app" -ForegroundColor Cyan
    Write-Host "Docs: Grant admin consent - https://learn.microsoft.com/entra/identity/enterprise-apps/grant-admin-consent" -ForegroundColor Cyan
    Write-Host "Portal: https://portal.azure.com -> Microsoft Entra ID -> App registrations -> $appName" -ForegroundColor Cyan
    Read-Host "Press Enter once .env's AGENT_IDENTITY_PUBLIC_CLIENT_ID (and AGENT_IDENTITY_TENANT_ID/AGENT_IDENTITY_UPN) are set"
}

# ---------------------------------------------------------------------
# Step 3: Bootstrap the Agent Identity's MSAL token cache
# ---------------------------------------------------------------------
Step-Header 3 "Bootstrap the Agent Identity's MSAL token cache"
if (-not (Should-Skip 3)) {
    Write-Host "This step requires INTERACTIVE sign-in as the Agent Identity user (device code flow)." -ForegroundColor Yellow
    Write-Host "You will see 3 device-code prompts (Graph, Fabric, SQL) - complete each in a browser." -ForegroundColor Yellow
    Write-Host "For EACH prompt: go to https://microsoft.com/devicelogin, enter the code shown in this" -ForegroundColor Cyan
    Write-Host "terminal, then sign in with the Agent Identity's UPN (from .env: AGENT_IDENTITY_UPN) and its" -ForegroundColor Cyan
    Write-Host "password (the one set/reset for this user - see Step 1)." -ForegroundColor Cyan
    Write-Host "Docs: Device code flow - https://learn.microsoft.com/entra/identity-platform/v2-oauth2-device-code" -ForegroundColor Cyan
    python "$RepoRoot\shared\bootstrap_agent_identity_tokens.py"
    if (-not $?) { throw "Token bootstrap failed - see output above." }
}

# ---------------------------------------------------------------------
# Step 4: Create the CaseThreadMap table
# ---------------------------------------------------------------------
Step-Header 4 "Create the CaseThreadMap table"
if (-not (Should-Skip 4)) {
    Write-Host "This step is fully automated - no manual portal action needed." -ForegroundColor Yellow
    Write-Host "It requires CASETHREADMAP_FABRIC_SQL_ENDPOINT and CASETHREADMAP_FABRIC_SQL_DATABASE in .env." -ForegroundColor Cyan
    Write-Host "Where to find them: https://app.fabric.microsoft.com -> open your Fabric SQL Database item ->" -ForegroundColor Cyan
    Write-Host "Settings (gear icon) -> Connection strings -> copy the Server and Database values shown there." -ForegroundColor Cyan
    Write-Host "Docs: Fabric SQL Database connectivity - https://learn.microsoft.com/fabric/database/sql/connect" -ForegroundColor Cyan
    python "$RepoRoot\shared\create_case_thread_map.py"
    if (-not $?) { throw "CaseThreadMap table creation failed - see output above." }
}

# ---------------------------------------------------------------------
# Step 5: Deploy the Work IQ webapp
# ---------------------------------------------------------------------
Step-Header 5 "Deploy the Work IQ webapp"
if (-not (Should-Skip 5)) {
    Write-Host "Ensure WORKIQ_RESOURCE_GROUP and WORKIQ_WEBAPP_NAME are set in .env." -ForegroundColor Yellow
    Write-Host "WORKIQ_API_KEY must also be set - this is a secret YOU invent (not looked up anywhere)." -ForegroundColor Yellow
    Write-Host "  Generate one: python -c `"import secrets;print(secrets.token_urlsafe(24))`"" -ForegroundColor Cyan
    Write-Host "The script will auto-create the App Service Plan/Web App if they do not already exist" -ForegroundColor Yellow
    Write-Host "(Linux/Python, SKU from WORKIQ_SKU or B1 default, location from WORKIQ_LOCATION or westus3 default)." -ForegroundColor Yellow
    Write-Host "Docs: Create an App Service - https://learn.microsoft.com/azure/app-service/quickstart-python" -ForegroundColor Cyan
    Write-Host "Docs: az webapp deploy (zip deploy) - https://learn.microsoft.com/cli/azure/webapp#az-webapp-deploy" -ForegroundColor Cyan
    python "$RepoRoot\triggers\webapp\deploy_webapp.py"
    if (-not $?) { throw "Webapp deployment failed - see output above." }
}

# ---------------------------------------------------------------------
# Step 6: Create the Foundry connection for the webapp API key
# ---------------------------------------------------------------------
Step-Header 6 "Create the Foundry connection for the webapp API key"
if (-not (Should-Skip 6)) {
    Write-Host "Ensure AZURE_SUBSCRIPTION_ID, FOUNDRY_RESOURCE_GROUP, FOUNDRY_ACCOUNT_NAME, FOUNDRY_PROJECT_NAME are set in .env." -ForegroundColor Yellow
    Write-Host "Docs: Foundry connections (Custom Keys) - https://learn.microsoft.com/azure/ai-foundry/how-to/connections-add" -ForegroundColor Cyan
    python "$RepoRoot\foundry\create_workiq_connection.py"
    if (-not $?) { throw "Foundry connection creation failed - see output above." }
}

# ---------------------------------------------------------------------
# Step 7: Create the Foundry orchestrator agent
# ---------------------------------------------------------------------
Step-Header 7 "Create the Foundry orchestrator agent"
if (-not (Should-Skip 7)) {
    Write-Host "Docs: Azure AI Foundry Agent Service - https://learn.microsoft.com/azure/ai-services/agents/overview" -ForegroundColor Cyan
    Write-Host "Docs: Connected agents / OpenAPI tools - https://learn.microsoft.com/azure/ai-services/agents/how-to/tools/openapi-spec" -ForegroundColor Cyan
    python "$RepoRoot\foundry\create_orchestrator_agent.py"
    if (-not $?) { throw "Orchestrator agent creation failed - see output above." }
}

# ---------------------------------------------------------------------
# Step 8: Deploy the Logic Apps
# ---------------------------------------------------------------------
Step-Header 8 "Deploy the Logic Apps"
if (-not (Should-Skip 8)) {
    Write-Host "This step is now MOSTLY automated. parameters.json is generated automatically from" -ForegroundColor Yellow
    Write-Host ".env + foundry\orchestrator_agent_id.txt. The Office 365 API connection and both Logic" -ForegroundColor Yellow
    Write-Host "App resources are created via ARM REST - no manual Notepad editing needed." -ForegroundColor Yellow
    Write-Host "Ensure LOGICAPP_RESOURCE_GROUP (or WORKIQ_RESOURCE_GROUP) and AZURE_SUBSCRIPTION_ID are set in .env." -ForegroundColor Cyan
    Write-Host "Docs: Create a Consumption Logic App - https://learn.microsoft.com/azure/logic-apps/quickstart-create-example-consumption-workflow" -ForegroundColor Cyan
    python "$RepoRoot\triggers\logicapp\deploy_logic_apps.py"
    if (-not $?) { throw "Logic Apps deployment failed - see output above." }
    Confirm-Continue @"
ONE MANUAL STEP still required (cannot be automated - OAuth requires a signed-in human):
  Authorize the Office 365 connection against the FNOL intake mailbox.
  Go to: https://portal.azure.com -> Resource groups -> (your resource group) ->
  'office365' connection -> Edit API connection -> Authorize -> sign in as the
  FNOL intake mailbox account -> Save.
  Docs: https://learn.microsoft.com/connectors/office365/
"@ | Out-Null
}

# ---------------------------------------------------------------------
# Step 9: Grant the Logic Apps' Managed Identities their roles
# ---------------------------------------------------------------------
Step-Header 9 "Grant the Logic Apps' Managed Identities their roles"
if (-not (Should-Skip 9)) {
    Write-Host "This step is now fully automated - it looks up both Logic Apps' managed identities and the" -ForegroundColor Yellow
    Write-Host "Foundry account's resource id directly from Azure/`.env`, then grants:" -ForegroundColor Yellow
    Write-Host "  1. 'Cognitive Services User' (Azure RBAC) to Logic App #1's identity, scoped to the Foundry account." -ForegroundColor Yellow
    Write-Host "  2. The Graph APPLICATION permission ChannelMessage.Read.All to Logic App #2's identity" -ForegroundColor Yellow
    Write-Host "     (via a direct Microsoft Graph API call - this is not an Azure RBAC role, so it needs its own step)." -ForegroundColor Yellow
    Write-Host "Docs: Managed identity for Logic Apps - https://learn.microsoft.com/azure/logic-apps/authenticate-with-managed-identity" -ForegroundColor Cyan
    Write-Host "Docs: Grant app roles to a managed identity - https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/how-to-assign-app-role-managed-identity-powershell" -ForegroundColor Cyan

    $logicAppRg = $env:LOGICAPP_RESOURCE_GROUP
    if (-not $logicAppRg) { $logicAppRg = $env:WORKIQ_RESOURCE_GROUP }
    $la1Name = if ($env:LOGICAPP_EMAIL_INTAKE_NAME) { $env:LOGICAPP_EMAIL_INTAKE_NAME } else { "la-fnol-email-intake" }
    $la2Name = if ($env:LOGICAPP_REPLY_POLLER_NAME) { $env:LOGICAPP_REPLY_POLLER_NAME } else { "la-fnol-teams-reply-poller" }

    Write-Host "Looking up Logic App #1 ('$la1Name') managed identity principal id..." -ForegroundColor Cyan
    $la1Principal = az resource show --resource-group $logicAppRg --name $la1Name --resource-type Microsoft.Logic/workflows --query "identity.principalId" -o tsv
    Write-Host "Looking up Logic App #2 ('$la2Name') managed identity principal id..." -ForegroundColor Cyan
    $la2Principal = az resource show --resource-group $logicAppRg --name $la2Name --resource-type Microsoft.Logic/workflows --query "identity.principalId" -o tsv
    Write-Host "Looking up Foundry account's resource id..." -ForegroundColor Cyan
    $foundryScope = az cognitiveservices account show --name $env:FOUNDRY_ACCOUNT_NAME --resource-group $env:FOUNDRY_RESOURCE_GROUP --query id -o tsv

    if (-not $la1Principal -or -not $la2Principal -or -not $foundryScope) {
        Write-Host "Could not auto-discover one or more values. Falling back to manual entry." -ForegroundColor Yellow
        if (-not $la1Principal) { $la1Principal = Read-Host "Enter Logic App #1 (email intake) Managed Identity principal id" }
        if (-not $la2Principal) { $la2Principal = Read-Host "Enter Logic App #2 (reply poller) Managed Identity principal id" }
        if (-not $foundryScope) { $foundryScope = Read-Host "Enter the Foundry account's full resource id (/subscriptions/.../accounts/<name>)" }
    }

    # 'Foundry User' is the current Microsoft-recommended role for agents/threads/messages
    # data-plane operations (write/read). 'Cognitive Services User' is legacy/no longer
    # sufficient for some agent operations ("Principal does not have access to API/Operation")
    # even though it grants the base Cognitive Services data-plane read/write actions - so both
    # roles are granted here for safety/back-compat.
    if ($la1Principal -and $foundryScope) {
        az role assignment create --assignee $la1Principal --role "Cognitive Services User" --scope $foundryScope -o none
        az role assignment create --assignee $la1Principal --role "Foundry User" --scope $foundryScope -o none
        Write-Host "Granted 'Cognitive Services User' and 'Foundry User' to Logic App #1's managed identity." -ForegroundColor Green
    }

    if ($la2Principal -and $foundryScope) {
        az role assignment create --assignee $la2Principal --role "Cognitive Services User" --scope $foundryScope -o none
        az role assignment create --assignee $la2Principal --role "Foundry User" --scope $foundryScope -o none
        Write-Host "Granted 'Cognitive Services User' and 'Foundry User' to Logic App #2's managed identity (needed for Post_reply_to_Foundry_thread / Start_followup_run / Get_followup_run_status / Get_followup_latest_message)." -ForegroundColor Green
    }

    if ($la2Principal) {
        Write-Host "Granting Graph app role 'ChannelMessage.Read.All' to Logic App #2's managed identity..." -ForegroundColor Cyan
        $graphSpId = az ad sp show --id 00000003-0000-0000-c000-000000000000 --query id -o tsv
        $appRoleId = az ad sp show --id 00000003-0000-0000-c000-000000000000 --query "appRoles[?value=='ChannelMessage.Read.All'].id" -o tsv
        $bodyPath = Join-Path $env:TEMP "fnol_approle_body.json"
        @{ principalId = $la2Principal; resourceId = $graphSpId; appRoleId = $appRoleId } | ConvertTo-Json -Compress | Out-File -FilePath $bodyPath -Encoding utf8
        $existing = az rest --method GET --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$la2Principal/appRoleAssignments" --query "value[?appRoleId=='$appRoleId']" -o tsv
        if ($existing) {
            Write-Host "ChannelMessage.Read.All already granted to Logic App #2's managed identity - skipping." -ForegroundColor Yellow
        } else {
            az rest --method POST --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$la2Principal/appRoleAssignments" --body "@$bodyPath" --headers "Content-Type=application/json" -o none
            Write-Host "Granted Graph app role 'ChannelMessage.Read.All' to Logic App #2's managed identity." -ForegroundColor Green
        }
        Remove-Item $bodyPath -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Deployment steps complete. Run the verification checklist next" -ForegroundColor Green
Write-Host " (docs/End_to_End_Deployment_Guide.docx, Section 7)." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
