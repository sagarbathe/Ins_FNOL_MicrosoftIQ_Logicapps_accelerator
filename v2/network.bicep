param prefix string
param environmentId string
param location string
param webName string

var tags = {
  solution: 'fnol-delegated-v2'
  environmentId: environmentId
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: '${prefix}state'
}
resource web 'Microsoft.Web/sites@2023-12-01' existing = {
  name: webName
}
resource network 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${prefix}-network'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.243.0.0/24'] }
    subnets: [
      {
        name: 'web-integration'
        properties: {
          addressPrefix: '10.243.0.0/26'
          delegations: [{
            name: 'app-service'
            properties: { serviceName: 'Microsoft.Web/serverFarms' }
          }]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.243.0.64/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}
resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zone
  name: '${prefix}-network'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: network.id }
  }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-state-private'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${network.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: 'private-state'
      properties: {
        privateLinkServiceId: storage.id
        groupIds: ['blob']
      }
    }]
  }
}
resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [{
      name: 'blob'
      properties: { privateDnsZoneId: zone.id }
    }]
  }
}
resource integration 'Microsoft.Web/sites/networkConfig@2023-12-01' = {
  parent: web
  name: 'virtualNetwork'
  properties: { subnetResourceId: '${network.id}/subnets/web-integration' }
  dependsOn: [zoneGroup, link]
}

output networkId string = network.id
output privateEndpointId string = endpoint.id
output integrationSubnetId string = '${network.id}/subnets/web-integration'
