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
# Terraform state. Access policies, not Azure RBAC: the hackathon resource group grants Contributor, which
# cannot create role assignments but can edit a vault's access policies.
resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.compact_name}"
  resource_group_name        = local.resource_group_name
  location                   = local.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = false
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  tags                       = local.tags
}

# Whoever runs Terraform sets the Anthropic key.
resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id       = azurerm_key_vault.main.id
  tenant_id          = data.azurerm_client_config.current.tenant_id
  object_id          = data.azurerm_client_config.current.object_id
  secret_permissions = ["Get", "List", "Set", "Delete", "Purge"]
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

  # Storage is reached with the account key: managed identities would need role assignments, which the
  # hackathon resource group does not allow. The key ends up in the app settings and the Terraform state.
  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.main.primary_blob_endpoint}${azurerm_storage_container.deployment.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.main.primary_access_key

  runtime_name           = "python"
  runtime_version        = "3.13"
  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = 2048

  # Resolves the Key Vault reference below (access policy azurerm_key_vault_access_policy.functions).
  identity {
    type = "SystemAssigned"
  }

  # AzureWebJobsStorage is set by the provider from storage_access_key (setting it here shows a diff on every plan).
  app_settings = {
    DATA_STORAGE_CONNECTION_STRING = azurerm_storage_account.main.primary_connection_string
    ANTHROPIC_API_KEY              = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault.main.vault_uri}secrets/${local.anthropic_key_secret}/)"
    REPORT_TOKEN                   = random_password.report_token.result
    CLAUDE_MODEL                   = var.claude_model
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
}

resource "azurerm_key_vault_access_policy" "functions" {
  key_vault_id       = azurerm_key_vault.main.id
  tenant_id          = data.azurerm_client_config.current.tenant_id
  object_id          = azurerm_function_app_flex_consumption.api.identity[0].principal_id
  secret_permissions = ["Get"]
}
