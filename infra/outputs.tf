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
  value = azurerm_machine_learning_workspace.main.name
}

output "ml_compute" {
  value = azurerm_machine_learning_compute_cluster.cpu.name
}
