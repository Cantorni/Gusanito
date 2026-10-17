data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

locals {
  name                 = "${var.prefix}-${random_string.suffix.result}"
  compact_name         = "${var.prefix}${random_string.suffix.result}"
  anthropic_key_secret = "anthropic-api-key"
  tags                 = { project = "gusanito" }
}

# Hackathon subscriptions hand out a ready-made resource group: set resource_group_name to use it.
resource "azurerm_resource_group" "main" {
  count    = var.resource_group_name == null ? 1 : 0
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

data "azurerm_resource_group" "existing" {
  count = var.resource_group_name == null ? 0 : 1
  name  = var.resource_group_name
}

locals {
  resource_group_name = one(concat(azurerm_resource_group.main[*].name, data.azurerm_resource_group.existing[*].name))
  location            = one(concat(azurerm_resource_group.main[*].location, data.azurerm_resource_group.existing[*].location))
}

# One storage account: deployed code, Functions host state and analysis queue, report photos (blob),
# the reservoirs layer (blob) and reports (table).
resource "azurerm_storage_account" "main" {
  name                            = "st${local.compact_name}"
  resource_group_name             = local.resource_group_name
  location                        = local.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "deployment" {
  name                  = "deployment"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

# The app reaches storage with this identity, no keys. It exists before the app, so the
# permissions are in place when the host starts.
resource "azurerm_user_assigned_identity" "functions" {
  name                = "id-${local.name}"
  resource_group_name = local.resource_group_name
  location            = local.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "storage" {
  for_each = toset([
    "Storage Blob Data Owner",
    "Storage Queue Data Contributor",
    "Storage Table Data Contributor",
  ])

  scope                = azurerm_storage_account.main.id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.functions.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  resource_group_name = local.resource_group_name
  location            = local.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  resource_group_name = local.resource_group_name
  location            = local.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "other"
  tags                = local.tags
}

# The Anthropic key lives in Key Vault and is set by hand (see README), so it never lands in the
# Terraform state.
resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.compact_name}"
  resource_group_name        = local.resource_group_name
  location                   = local.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  tags                       = local.tags
}

# Whoever runs Terraform can load data from a laptop (backend/tools/seed_demo.py, publish_layer.py, run_alerts.py):
# the storage account takes Entra ID tokens for data, and owning the subscription does not grant data access.
resource "azurerm_role_assignment" "storage_deployer" {
  for_each = toset(["Storage Blob Data Contributor", "Storage Table Data Contributor"])

  scope                = azurerm_storage_account.main.id
  role_definition_name = each.value
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "key_vault_deployer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Token for the receiver page (agronomists reading reports).
resource "random_password" "report_token" {
  length  = 32
  special = false
}

# The PWA and the receiver page.
resource "azurerm_static_web_app" "pwa" {
  name                = "stapp-${local.name}"
  resource_group_name = local.resource_group_name
  location            = var.static_web_app_location
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = local.tags
}

resource "azurerm_service_plan" "functions" {
  name                = "asp-${local.name}"
  resource_group_name = local.resource_group_name
  location            = local.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "api" {
  name                = "func-${local.name}"
  resource_group_name = local.resource_group_name
  location            = local.location
  service_plan_id     = azurerm_service_plan.functions.id
  https_only          = true

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.main.primary_blob_endpoint}${azurerm_storage_container.deployment.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.functions.id

  runtime_name           = "python"
  runtime_version        = "3.13"
  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = 2048

  # The user-assigned identity reaches storage; the system-assigned one resolves Key Vault
  # references (the app cannot pick another identity for that).
  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.functions.id]
  }

  app_settings = {
    AzureWebJobsStorage__accountName = azurerm_storage_account.main.name
    AzureWebJobsStorage__credential  = "managedidentity"
    AzureWebJobsStorage__clientId    = azurerm_user_assigned_identity.functions.client_id
    AZURE_CLIENT_ID                  = azurerm_user_assigned_identity.functions.client_id
    DATA_STORAGE_ACCOUNT             = azurerm_storage_account.main.name
    ANTHROPIC_API_KEY                = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault.main.vault_uri}secrets/${local.anthropic_key_secret}/)"
    REPORT_TOKEN                     = random_password.report_token.result
    CLAUDE_MODEL                     = var.claude_model
    # Base of the offline link in the alert SMS. SMS_SENDER is left unset: simulated, nothing leaves Azure.
    PWA_URL = "https://${azurerm_static_web_app.pwa.default_host_name}"
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string

    cors {
      allowed_origins = concat(["https://${azurerm_static_web_app.pwa.default_host_name}"], var.extra_cors_origins)
    }
  }

  tags = local.tags

  depends_on = [azurerm_role_assignment.storage]
}

resource "azurerm_role_assignment" "key_vault_functions" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_function_app_flex_consumption.api.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
