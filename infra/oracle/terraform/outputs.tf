output "instance_private_ip" {
  description = "Private IP of the POC VM."
  value       = oci_core_instance.this.private_ip
}

output "instance_public_ip" {
  description = "Public IP (public_ssh mode only; null in bastion mode)."
  value       = oci_core_instance.this.public_ip == "" ? null : oci_core_instance.this.public_ip
}

output "bastion_id" {
  description = "OCID of the OCI Bastion (bastion mode only)."
  value       = one(oci_bastion_bastion.this[*].id)
}

output "ssh_tunnel_command_template" {
  description = "Command templates only. Replace the angle-bracket placeholders; no secret is included."
  value = local.bastion_mode ? join("\n", [
    "# 1. Create a port-forwarding Bastion session (owner-run, short TTL):",
    "oci bastion session create-port-forwarding --bastion-id ${coalesce(one(oci_bastion_bastion.this[*].id), "")} --target-private-ip ${oci_core_instance.this.private_ip} --target-port 22 --ssh-public-key-file <path-to-public-key.pub> --session-ttl 10800",
    "# 2. Run the ssh command shown in the session details (it forwards a local port to VM port 22), then:",
    "ssh -i <path-to-private-key> -p <local-forwarded-port> -N -L 3001:127.0.0.1:3001 -L 8010:127.0.0.1:8010 -L 8080:127.0.0.1:8080 deploy@127.0.0.1",
  ]) : "ssh -i <path-to-private-key> -N -L 3001:127.0.0.1:3001 -L 8010:127.0.0.1:8010 -L 8080:127.0.0.1:8080 deploy@${coalesce(oci_core_instance.this.public_ip, "<public-ip>")}"
}
