terraform {
  required_version = ">= 1.9.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 7.0.0, < 9.0.0"
    }
  }
}

# Authentication comes from the owner's OCI CLI/SDK config profile on the machine that runs
# Terraform. No credential, OCID or key is stored in this repository.
provider "oci" {
  region              = var.home_region
  config_file_profile = var.oci_config_profile
}
