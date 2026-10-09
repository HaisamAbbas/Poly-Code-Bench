# ---- Owner gates: nothing is created until these are set deliberately. ----

variable "owner_spend_acknowledged" {
  type        = bool
  default     = false
  description = "Set true only after the owner has confirmed the OCI account state, spend limit, billing alert and shutdown date."
}

variable "home_region" {
  type        = string
  default     = ""
  description = "The home region identifier of the tenancy, taken from the console region list. Always Free A1 capacity must be created here."
  validation {
    condition     = var.home_region == "" || can(regex("^[a-z]{2}-[a-z0-9-]+-[0-9]+$", var.home_region))
    error_message = "home_region must look like an OCI region identifier, or be left empty (which blocks creation)."
  }
}

variable "free_tier_only" {
  type        = bool
  default     = true
  description = "Refuse anything but VM.Standard.A1.Flex with at most 2 OCPUs and 12 GB. Setting false is unsupported by this kit and may incur charges."
}

variable "oci_config_profile" {
  type        = string
  default     = "DEFAULT"
  description = "Profile name in the owner local OCI config file."
}

# ---- Placement and access ----

variable "compartment_ocid" {
  type        = string
  default     = ""
  description = "Existing compartment (or the tenancy root) to create resources in. This kit does not create a compartment."
  validation {
    condition     = var.compartment_ocid == "" || can(regex("^ocid1\\.(compartment|tenancy)\\.", var.compartment_ocid))
    error_message = "compartment_ocid must be a compartment or tenancy OCID, or empty (which blocks creation)."
  }
}

variable "access_mode" {
  type        = string
  default     = "bastion"
  description = "bastion: private subnet reached through the OCI Bastion service (default). public_ssh: public subnet with SSH open only to allowed_ssh_cidr."
  validation {
    condition     = contains(["bastion", "public_ssh"], var.access_mode)
    error_message = "access_mode must be bastion or public_ssh."
  }
}

variable "public_web" {
  type        = bool
  default     = false
  description = "Open TCP 80/443 to the internet for the reverse proxy (requires access_mode = public_ssh). SSH stays limited to allowed_ssh_cidr."
}

variable "allowed_ssh_cidr" {
  type        = string
  description = "The one CIDR allowed to reach SSH (bastion client allow-list, or the security-list source in public_ssh mode). Required; use your own /32."
  validation {
    condition     = can(cidrhost(var.allowed_ssh_cidr, 0)) && !contains(["0.0.0.0/0", "::/0"], var.allowed_ssh_cidr)
    error_message = "allowed_ssh_cidr must be a valid IPv4 CIDR and must not be 0.0.0.0/0."
  }
}

variable "ssh_public_key" {
  type        = string
  default     = ""
  description = "OpenSSH PUBLIC key for the deploy user and the image default user. Never put a private key here."
  validation {
    condition     = !can(regex("PRIVATE KEY", var.ssh_public_key))
    error_message = "ssh_public_key must be a public key."
  }
}

variable "availability_domain_index" {
  type        = number
  default     = 0
  description = "Index into the home region availability domains. Try another index if A1 capacity is reported unavailable."
}

variable "vcn_cidr" {
  type        = string
  default     = "10.77.0.0/24"
  description = "VCN block; a single subnet uses all of it."
  validation {
    condition     = can(cidrhost(var.vcn_cidr, 0))
    error_message = "vcn_cidr must be a valid IPv4 CIDR block."
  }
}

# ---- Compute ----

variable "shape" {
  type        = string
  default     = "VM.Standard.A1.Flex"
  description = "Always Free Ampere A1 shape."
}

variable "ocpus" {
  type        = number
  default     = 2
  description = "Ampere A1 OCPUs (the Always Free total is 2)."
}

variable "memory_in_gbs" {
  type        = number
  default     = 12
  description = "Memory in GB (the Always Free total is 12)."
}

variable "boot_volume_size_in_gbs" {
  type        = number
  default     = 100
  description = "Boot volume size. Always Free block storage totals 200 GB across all volumes."
  validation {
    condition     = var.boot_volume_size_in_gbs >= 50 && var.boot_volume_size_in_gbs <= 200
    error_message = "boot_volume_size_in_gbs must be between 50 and 200."
  }
}

variable "image_id" {
  type        = string
  default     = ""
  description = "OCID of an Ubuntu aarch64 image in the home region. Empty selects the latest Canonical Ubuntu 24.04 image compatible with the shape."
  validation {
    condition     = var.image_id == "" || can(regex("^ocid1\\.image\\.", var.image_id))
    error_message = "image_id must be an image OCID, or empty (auto-select)."
  }
}

variable "instance_name" {
  type        = string
  default     = "pcb-poc"
  description = "Display-name prefix for all resources."
}
