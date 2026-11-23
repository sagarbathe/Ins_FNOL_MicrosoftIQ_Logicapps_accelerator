targetScope = 'resourceGroup'

param location string
param hostingLocation string = location
param searchLocation string = location
param vaultAllowedIpv4Cidrs array = []
@allowed(['B1', 'P0v3', 'P1v3'])
param hostingSku string = 'B1'
@minLength(6)
@maxLength(16)
param prefix string
param environmentId string
param operatorId string

var tags = {
  solution: 'fnol-delegated-v2'
  environmentId: environmentId
}
var blobRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var vaultReadRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}state'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource state 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'private-state'
  properties: { publicAccess: 'None' }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-vault'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    publicNetworkAccess: empty(vaultAllowedIpv4Cidrs) ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: [for cidr in vaultAllowedIpv4Cidrs: { value: cidr }]
      virtualNetworkRules: []
    }
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    accessPolicies: []
  }
}
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${prefix}-ai'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: '${prefix}-ai'
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  dependsOn: [model]
  name: 'fnol-v2'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'FNOL delegated v2'
    description: 'Isolated user-delegated synthetic FNOL pilot'
  }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'investigation'
  sku: { name: 'GlobalStandard', capacity: 30 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-5-mini', version: '2025-08-07' }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}
resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: '${prefix}-search'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    disableLocalAuth: true
    semanticSearch: 'free'
    publicNetworkAccess: 'enabled'
  }
}
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-plan'
  location: hostingLocation
  tags: tags
  kind: 'linux'
  sku: { name: hostingSku, tier: hostingSku == 'B1' ? 'Basic' : 'PremiumV3', capacity: 1 }
  properties: { reserved: true }
}
resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: '${prefix}-web'
  location: hostingLocation
  tags: tags
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      healthCheckPath: '/healthz'
      appCommandLine: 'gunicorn --bind=0.0.0.0:8000 --workers=2 --threads=4 --timeout=180 v2.wsgi:app'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
      ]
    }
  }
}
resource webBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, web.id, blobRole)
  scope: storage
  properties: { principalId: web.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobRole }
}
resource operatorBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, operatorId, blobRole)
  scope: storage
  properties: { principalId: operatorId, principalType: 'User', roleDefinitionId: blobRole }
}
resource webVault 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, web.id, vaultReadRole)
  scope: vault
  properties: { principalId: web.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: vaultReadRole }
}
resource operatorVault 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, operatorId, 'secrets-officer')
  scope: vault
  properties: {
    principalId: operatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}
resource operatorFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, operatorId, 'foundry-user')
  scope: foundry
  properties: {
    principalId: operatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}
resource operatorSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, operatorId, 'search-management')
  scope: search
  properties: {
    principalId: operatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
  }
}
resource operatorSearchData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, operatorId, 'search-data')
  scope: search
  properties: {
    principalId: operatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}
resource projectSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, project.id, 'search-reader')
  scope: search
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
  }
}
output webName string = web.name
output webUrl string = 'https://${web.properties.defaultHostName}'
output storageUrl string = storage.properties.primaryEndpoints.blob
output vaultUrl string = vault.properties.vaultUri
output foundryName string = foundry.name
output projectId string = project.id
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output searchUrl string = 'https://${search.name}.search.windows.net'
output searchId string = search.id
