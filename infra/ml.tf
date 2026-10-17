# Detection engine: Azure ML trains the reservoir model from the field reports. It reuses the storage,
# Key Vault and Application Insights of the rest of the stack. Off unless enable_ml is true: the app does not
# need it to run, and hackathon subscriptions may lack the quota.
resource "azurerm_machine_learning_workspace" "main" {
  count                   = var.enable_ml ? 1 : 0
  name                    = "mlw-${local.name}"
  resource_group_name     = local.resource_group_name
  location                = local.location
  application_insights_id = azurerm_application_insights.main.id
  key_vault_id            = azurerm_key_vault.main.id
  storage_account_id      = azurerm_storage_account.main.id
  tags                    = local.tags

  identity {
    type = "SystemAssigned"
  }
}

# Scales to zero: no cost while nothing trains.
resource "azurerm_machine_learning_compute_cluster" "cpu" {
  count                         = var.enable_ml ? 1 : 0
  name                          = "cpu-cluster"
  location                      = local.location
  machine_learning_workspace_id = azurerm_machine_learning_workspace.main[0].id
  vm_priority                   = "Dedicated"
  vm_size                       = var.ml_compute_vm_size

  scale_settings {
    min_node_count                       = 0
    max_node_count                       = var.ml_compute_max_nodes
    scale_down_nodes_after_idle_duration = "PT15M"
  }

  identity {
    type = "SystemAssigned"
  }
}

# Jobs read the training table and write the reservoirs layer to the shared storage with these identities.
resource "azurerm_role_assignment" "ml_storage" {
  for_each = var.enable_ml ? {
    workspace = azurerm_machine_learning_workspace.main[0].identity[0].principal_id
    cluster   = azurerm_machine_learning_compute_cluster.cpu[0].identity[0].principal_id
  } : {}

  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
  principal_type       = "ServicePrincipal"
}
