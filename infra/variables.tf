variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group to deploy into. Null creates a new one."
  type        = string
  default     = null
}

variable "prefix" {
  description = "Prefix for resource names (lowercase letters and digits)."
  type        = string
  default     = "gusanito"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.prefix))
    error_message = "Use 3 to 10 lowercase letters or digits, starting with a letter."
  }
}

variable "location" {
  description = "Azure region for the API and data. Must support Flex Consumption (az functionapp list-flexconsumption-locations)."
  type        = string
  default     = "germanywestcentral"
}

variable "static_web_app_location" {
  description = "Region for the PWA hosting. Static Web Apps only runs in a few regions (e.g. eastus2, westus2, westeurope)."
  type        = string
  default     = "eastus2"
}

variable "extra_cors_origins" {
  description = "Extra origins allowed to call the API from a browser, besides the deployed PWA."
  type        = list(string)
  default     = ["http://127.0.0.1:8787", "http://localhost:8787"]
}

variable "claude_model" {
  description = "Claude model that analyses the photos and drafts alerts."
  type        = string
  default     = "claude-opus-5-5"
}

variable "maximum_instance_count" {
  description = "Maximum instances the Function App scales out to."
  type        = number
  default     = 40
}

variable "enable_ml" {
  description = "Create the Azure ML workspace and training cluster (infra/ml.tf). The app runs without them. Needs permission to create role assignments (Owner or User Access Administrator)."
  type        = bool
  default     = false
}

variable "ml_compute_vm_size" {
  description = "VM size of the Azure ML training cluster (CPU is enough for the tabular model)."
  type        = string
  default     = "Standard_DS3_v2"
}

variable "ml_compute_max_nodes" {
  description = "Maximum nodes of the training cluster. It scales to zero when idle."
  type        = number
  default     = 2
}
