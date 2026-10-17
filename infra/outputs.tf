output "resource_group" {
  value = local.resource_group_name
}

output "function_app" {
  value = azurerm_function_app_flex_consumption.api.name
}

output "api_url" {
  value = "https://${azurerm_function_app_flex_consumption.api.default_hostname}/api"
}

output "pwa_url" {
  value = "https://${azurerm_static_web_app.pwa.default_host_name}"
}

output "storage_account" {
  value = azurerm_storage_account.main.name
}

# For the data tools run from a laptop (backend/tools/seed_demo.py, publish_layer.py, run_alerts.py).
output "storage_connection_string" {
  value     = azurerm_storage_account.main.primary_connection_string
  sensitive = true
}

output "key_vault" {
  value = azurerm_key_vault.main.name
}

output "report_token" {
  value     = random_password.report_token.result
  sensitive = true
}

output "static_web_app_deployment_token" {
  value     = azurerm_static_web_app.pwa.api_key
  sensitive = true
}

output "ml_workspace" {
  value = one(azurerm_machine_learning_workspace.main[*].name)
}

output "ml_compute" {
  value = one(azurerm_machine_learning_compute_cluster.cpu[*].name)
}
