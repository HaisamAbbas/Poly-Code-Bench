locals {
  bastion_mode = var.access_mode == "bastion"
  image_auto   = var.image_id == ""
  image_ready  = local.image_auto || (can(regex("^ocid1\\.image\\.", var.image_id)) && !strcontains(var.image_id, "REPLACE_ME"))
  image_ocid   = local.image_auto ? data.oci_core_images.ubuntu_arm[0].images[0].id : var.image_id
  comp_ready   = can(regex("^ocid1\\.(compartment|tenancy)\\.", var.compartment_ocid)) && !strcontains(var.compartment_ocid, "REPLACE_ME")
  free_shape   = var.shape == "VM.Standard.A1.Flex" && var.ocpus >= 1 && var.ocpus <= 2 && var.memory_in_gbs >= 1 && var.memory_in_gbs <= 12
}

# Every other resource depends on this, so a failed precondition creates nothing.
resource "terraform_data" "guards" {
  lifecycle {
    precondition {
      condition     = var.owner_spend_acknowledged
      error_message = "Refusing to create resources: set owner_spend_acknowledged = true only after the owner has confirmed account status, spend limit, billing alert and shutdown date."
    }
    precondition {
      condition     = var.home_region != ""
      error_message = "Refusing to create resources: home_region is not set."
    }
    precondition {
      condition     = !var.free_tier_only || local.free_shape
      error_message = "free_tier_only: only VM.Standard.A1.Flex with 1-2 OCPUs and at most 12 GB memory is allowed."
    }
    precondition {
      condition     = local.comp_ready
      error_message = "compartment_ocid must be set to a real compartment or tenancy OCID."
    }
    precondition {
      condition     = local.image_ready
      error_message = "image_id must be empty (auto-select Ubuntu 24.04 aarch64) or a real image OCID from the home region."
    }
    precondition {
      condition     = !var.public_web || !local.bastion_mode
      error_message = "public_web requires access_mode = \"public_ssh\" (the VM needs a public IP)."
    }
    precondition {
      condition     = length(trimspace(var.ssh_public_key)) > 0
      error_message = "ssh_public_key must be set to an OpenSSH public key."
    }
  }
}

data "oci_identity_availability_domains" "this" {
  compartment_id = var.compartment_ocid
  depends_on     = [terraform_data.guards]
}

# Latest Canonical Ubuntu 24.04 aarch64 platform image, used when image_id is empty.
data "oci_core_images" "ubuntu_arm" {
  count                    = local.image_auto ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
  depends_on               = [terraform_data.guards]
}

resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.instance_name}-vcn"
  dns_label      = "pcbpoc"
  depends_on     = [terraform_data.guards]
}

# Bastion mode: outbound-only NAT for apt, Docker and image pulls. Public mode: internet gateway.
resource "oci_core_nat_gateway" "this" {
  count          = local.bastion_mode ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.instance_name}-nat"
}

resource "oci_core_internet_gateway" "this" {
  count          = local.bastion_mode ? 0 : 1
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.instance_name}-igw"
  enabled        = true
}

resource "oci_core_route_table" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.instance_name}-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = local.bastion_mode ? one(oci_core_nat_gateway.this[*].id) : one(oci_core_internet_gateway.this[*].id)
  }
}

# Inbound: SSH only. In bastion mode the source is the subnet itself (the Bastion private
# endpoint lives there; the owner CIDR is enforced by the Bastion allow-list). In public_ssh
# mode the source is allowed_ssh_cidr. Nothing else is open; application ports stay on loopback.
resource "oci_core_security_list" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.instance_name}-sl"

  ingress_security_rules {
    protocol    = "6"
    source      = local.bastion_mode ? var.vcn_cidr : var.allowed_ssh_cidr
    source_type = "CIDR_BLOCK"
    description = "SSH only"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # public_web: HTTP (ACME challenge + redirect) and HTTPS for the reverse proxy only.
  dynamic "ingress_security_rules" {
    for_each = var.public_web ? [80, 443] : []
    content {
      protocol    = "6"
      source      = "0.0.0.0/0"
      source_type = "CIDR_BLOCK"
      description = "Public web (reverse proxy)"
      tcp_options {
        min = ingress_security_rules.value
        max = ingress_security_rules.value
      }
    }
  }

  egress_security_rules {
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    description      = "Outbound for packages and image pulls"
  }
}

resource "oci_core_subnet" "this" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = var.vcn_cidr
  display_name               = "${var.instance_name}-subnet"
  dns_label                  = "pcbpoc"
  prohibit_public_ip_on_vnic = local.bastion_mode
  route_table_id             = oci_core_route_table.this.id
  security_list_ids          = [oci_core_security_list.this.id]
}

resource "oci_bastion_bastion" "this" {
  count                        = local.bastion_mode ? 1 : 0
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  target_subnet_id             = oci_core_subnet.this.id
  name                         = "pcbpocbastion"
  client_cidr_block_allow_list = [var.allowed_ssh_cidr]
  max_session_ttl_in_seconds   = 10800
}

resource "oci_core_instance" "this" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.this.availability_domains[var.availability_domain_index].name
  display_name        = var.instance_name
  shape               = var.shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_in_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = local.image_ocid
    boot_volume_size_in_gbs = var.boot_volume_size_in_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.this.id
    assign_public_ip = !local.bastion_mode
  }

  agent_config {
    plugins_config {
      name          = "Bastion"
      desired_state = local.bastion_mode ? "ENABLED" : "DISABLED"
    }
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(templatefile("${path.module}/../cloud-init.yaml", { ssh_public_key = trimspace(var.ssh_public_key), public_web = var.public_web }))
  }

  depends_on = [terraform_data.guards]
}
