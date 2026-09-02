<#
.SYNOPSIS
    End-to-end deployment orchestrator for the Auto FNOL Triage accelerator.

.DESCRIPTION
    Builds every artifact the solution needs, including Fabric IQ (sample data,
    lakehouse load, ontology, data agent) and Foundry IQ (search index, knowledge
    agent) when they don't already exist, then continues with the Agent Identity,
    Work IQ, and Logic Apps deployment flow. Each step checks whether its artifact
    is already present (via .env values or generated id files) and skips the
    build automatically if so - use -SkipSteps to force-skip any step regardless.
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

Step-Header 0 "Prerequisites check"
if (-not (Should-Skip 0)) {
    az account show -o none
    if (-not $?) { throw "Not logged in to Azure CLI. Run 'az login' first." }
    python --version
    if (-not $?) { throw "Python not found on PATH." }
    if (-not (Test-Path "$RepoRoot\.env")) {
        Copy-Item "$RepoRoot\.env.example" "$RepoRoot\.env"
        notepad "$RepoRoot\.env"
        Read-Host "Press Enter once .env is filled in"
    }
    Write-Host "Prerequisites OK." -ForegroundColor Green
}

Step-Header 1 "Generate synthetic Auto FNOL data"
if (-not (Should-Skip 1)) {
    $outDir = if ($env:DATAGEN_OUTPUT_DIR) { $env:DATAGEN_OUTPUT_DIR } else { "datagen\output" }
    if ((Test-Path "$RepoRoot\$outDir") -and (Get-ChildItem "$RepoRoot\$outDir" -Filter *.csv -ErrorAction SilentlyContinue)) {
        Write-Host "Synthetic data already present in $outDir - skipping generation." -ForegroundColor Yellow
    } else {
        python "$RepoRoot\datagen\generate_fnol_data.py"
        if (-not $?) { throw "Data generation failed." }
    }
}

Step-Header 2 "Load synthetic data into the Fabric lakehouse"
if (-not (Should-Skip 2)) {
    if ($env:FABRIC_LAKEHOUSE_DATA_LOADED -eq "true") {
        Write-Host "FABRIC_LAKEHOUSE_DATA_LOADED=true - skipping lakehouse load." -ForegroundColor Yellow
    } else {
        python "$RepoRoot\datagen\load_to_lakehouse.py"
        if (-not $?) { throw "Lakehouse load failed." }
        Write-Host "Set FABRIC_LAKEHOUSE_DATA_LOADED=true in .env to skip this step on reruns." -ForegroundColor Yellow
    }
}

Step-Header 3 "Build Fabric IQ ontology and configure the data agent"
if (-not (Should-Skip 3)) {
    if ($env:FABRIC_DATA_AGENT_ID -or $env:FABRIC_ONTOLOGY_ID) {
        Write-Host "FABRIC_DATA_AGENT_ID / FABRIC_ONTOLOGY_ID already set - assuming Fabric IQ ontology and data agent already exist, skipping build." -ForegroundColor Yellow
    } else {
        python "$RepoRoot\fabric\create_ontology.py"
        if (-not $?) { throw "Ontology creation failed." }
        python "$RepoRoot\fabric\configure_data_agent.py"
        if (-not $?) { throw "Raw-table data-agent configuration failed." }
        Write-Host "Copy the generated fabric\ontology_id.txt value into .env as FABRIC_ONTOLOGY_ID (and FABRIC_DATA_AGENT_ID from the console output) so reruns skip this step." -ForegroundColor Yellow
        Write-Host "If Fabric has auto-generated a graph model for the ontology, set FABRIC_GRAPH_MODEL_ID in .env and later run fabric\configure_data_agent_ontology.py to switch the data agent from lakehouse tables to the governed ontology graph." -ForegroundColor Yellow
    }
}

Step-Header 4 "Build Foundry IQ search index and knowledge agent"
if (-not (Should-Skip 4)) {
    $agentIdFile = "$RepoRoot\foundry\foundry_knowledge_agent_id.txt"
    if ($env:FOUNDRY_KNOWLEDGE_AGENT_ID -or (Test-Path $agentIdFile)) {
        Write-Host "FOUNDRY_KNOWLEDGE_AGENT_ID already set or foundry/foundry_knowledge_agent_id.txt already exists - skipping Foundry IQ build." -ForegroundColor Yellow
    } else {
        python "$RepoRoot\foundry\build_search_index.py"
        if (-not $?) { throw "Search index build failed." }
        python "$RepoRoot\foundry\create_foundry_agent.py"
        if (-not $?) { throw "Foundry knowledge agent creation failed." }
        Write-Host "Created Foundry IQ knowledge agent id file: foundry/foundry_knowledge_agent_id.txt. Copy its value into .env as FOUNDRY_KNOWLEDGE_AGENT_ID so reruns skip this step." -ForegroundColor Green
    }
}

Step-Header 5 "Create the Agent Identity user"
if (-not (Should-Skip 5)) {
    $upn = Read-Host "Enter the Agent Identity's UPN (e.g. svc-fnol-agent@yourtenant.onmicrosoft.com)"
    $existing = az ad user show --id $upn -o none 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "User $upn already exists - skipping creation." -ForegroundColor Yellow
    } else {
        $pwd = Read-Host "Enter a strong temporary password for this user" -AsSecureString
        $pwdPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwd))
        az ad user create --display-name "FNOL Automation Agent" --user-principal-name $upn --password $pwdPlain --force-change-password-next-sign-in false -o none
        Write-Host "Created user $upn." -ForegroundColor Green
    }
    Confirm-Continue "Manual steps: assign Exchange+Teams license, add the user to the target Team, grant Fabric workspace Contributor, and grant Foundry User on the Foundry account before continuing." | Out-Null
}

Step-Header 6 "Register the Agent Identity public-client app"
if (-not (Should-Skip 6)) {
    $appName = "FNOL-AgentIdentity-GraphPublicClient"
    $existingApp = az ad app list --display-name $appName --query "[0].appId" -o tsv
    if ($existingApp) {
        $appId = $existingApp
    } else {
        $appJson = az ad app create --display-name $appName --sign-in-audience AzureADMyOrg --is-fallback-public-client true -o json | ConvertFrom-Json
        $appId = $appJson.appId
        az ad app permission add --id $appId --api 00000003-0000-0000-c000-000000000000 --api-permissions 570282fd-fa5c-430d-a7fd-fc8dc98a9dca=Scope 205e70e5-aba6-4c52-a976-6d2d46c48043=Scope e383f46e-2787-4529-855e-0e479a3ffac0=Scope f501c180-9344-439a-bca0-6cbf209fd270=Scope ebf0f66e-9fb1-49e4-a278-222f76911cf4=Scope -o none
        az ad app permission add --id $appId --api 00000009-0000-0000-c000-000000000000 --api-permissions d2bc95fc-440e-4b0e-bafd-97182de7aef5=Scope caf40b1a-f10e-4da1-86e4-5fda17eb2b07=Scope -o none
        az ad app permission add --id $appId --api 022907d3-0f1b-48f7-badc-1ba6abab6d66 --api-permissions c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9=Scope -o none
        Start-Sleep -Seconds 10
        az ad app permission admin-consent --id $appId -o none
    }
    Write-Host "AGENT_IDENTITY_PUBLIC_CLIENT_ID = $appId" -ForegroundColor Cyan
    Read-Host "Press Enter once .env values are updated"
}

Step-Header 7 "Bootstrap the Agent Identity's MSAL token cache"
if (-not (Should-Skip 7)) {
    python "$RepoRoot\shared\bootstrap_agent_identity_tokens.py"
    if (-not $?) { throw "Token bootstrap failed." }
}

Step-Header 8 "Create the CaseThreadMap table"
if (-not (Should-Skip 8)) {
    python "$RepoRoot\shared\create_case_thread_map.py"
    if (-not $?) { throw "CaseThreadMap creation failed." }
}

Step-Header 9 "Deploy the Work IQ webapp"
if (-not (Should-Skip 9)) {
    python "$RepoRoot\triggers\webapp\deploy_webapp.py"
    if (-not $?) { throw "Webapp deployment failed." }
}

Step-Header 10 "Create the Foundry connection for the webapp API key"
if (-not (Should-Skip 10)) {
    python "$RepoRoot\foundry\create_workiq_connection.py"
    if (-not $?) { throw "Foundry connection creation failed." }
}

Step-Header 11 "Create the Foundry orchestrator agent"
if (-not (Should-Skip 11)) {
    python "$RepoRoot\foundry\create_orchestrator_agent.py"
    if (-not $?) { throw "Orchestrator creation failed." }
}

Step-Header 12 "Deploy the Logic Apps"
if (-not (Should-Skip 12)) {
    python "$RepoRoot\triggers\logicapp\deploy_logic_apps.py"
    if (-not $?) { throw "Logic App deployment failed." }
    Confirm-Continue "Authorize the Office 365 connection in Azure Portal before continuing." | Out-Null
}

Step-Header 13 "Grant the Logic Apps' Managed Identities their roles"
if (-not (Should-Skip 13)) {
    Write-Host "Granting Foundry User / Cognitive Services User plus reply-poller Graph ChannelMessage.Read.All." -ForegroundColor Yellow
    $logicAppRg = $env:LOGICAPP_RESOURCE_GROUP
    if (-not $logicAppRg) { $logicAppRg = $env:WORKIQ_RESOURCE_GROUP }
    $la1Name = if ($env:LOGICAPP_EMAIL_INTAKE_NAME) { $env:LOGICAPP_EMAIL_INTAKE_NAME } else { "la-fnol-email-intake" }
    $la2Name = if ($env:LOGICAPP_REPLY_POLLER_NAME) { $env:LOGICAPP_REPLY_POLLER_NAME } else { "la-fnol-teams-reply-poller" }
    $la1Principal = az resource show --resource-group $logicAppRg --name $la1Name --resource-type Microsoft.Logic/workflows --query "identity.principalId" -o tsv
    $la2Principal = az resource show --resource-group $logicAppRg --name $la2Name --resource-type Microsoft.Logic/workflows --query "identity.principalId" -o tsv
    $foundryScope = az cognitiveservices account show --name $env:FOUNDRY_ACCOUNT_NAME --resource-group $env:FOUNDRY_RESOURCE_GROUP --query id -o tsv
    foreach ($principal in @($la1Principal, $la2Principal)) {
        if ($principal) {
            az role assignment create --assignee $principal --role "Cognitive Services User" --scope $foundryScope -o none
            az role assignment create --assignee $principal --role "Foundry User" --scope $foundryScope -o none
        }
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Deployment steps complete." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
