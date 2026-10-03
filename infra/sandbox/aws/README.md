# EC2 sandbox deployment plan

Prompt 33: this directory is now a reusable child module. It is composed by
`infra/terraform/modules/workers` into the per-environment stacks under
`infra/terraform/environments/`, which own the provider, account guard and state. Both stacks
pass `terraform validate` (AWS provider 6.36.0). Nothing has been applied: see
`docs/operations/staging-execution-plan.md`.

This module describes the private guest boundary only. It does not create an
AWS account, VPC, supervisor, or worker instance. It must not be applied until
an owner supplies the target account/region, budget limit, approved subnet and
security-group identities, reviewed AMI digest/provenance, and supervisor
principal. At Prompt 06 Terraform was unavailable; since Prompt 33 it is validated through the
`hashicorp/terraform:1.13` container, but it has never been planned or applied.

The approved AMI is expected to contain a pinned Docker Engine, every
allowlisted candidate image at its exact approved digest (preloaded before the
VM is isolated; runtime pulls are disabled), the
`infra/sandbox/guest/pcb-guest-control.py` program installed as
`/usr/local/sbin/pcb-guest-control`, an `sshd` account whose only authorized
key has `restrict,command="/usr/local/sbin/pcb-guest-control"`, and a pinned
SSH host key distributed to the supervisor's known-hosts file. Disable TCP,
agent, X11, and UNIX-socket forwarding for that account. The supervisor keeps
the corresponding private SSH key and AWS credentials outside the guest.

The module has no instance profile, disables the IMDS endpoint, assigns no
public IP, permits SSH only from the configured supervisor security group,
and has no VM egress. Solve, grading, and admission each receive their own
subnet, security group, and launch template. Candidate containers run with
`--network=none`, `--pull=never`, no Docker socket bind, and no cloud identity.
The HashiCorp AWS security-group resource removes AWS's default allow-all
egress rule when it creates a new group and adds outbound access only when
declared; the deployment attestation also rejects any egress permissions
observed on a live lane group ([provider documentation](https://registry.terraform.io/providers/hashicorp/aws/6.36.0/docs/resources/security_group)).

The module deliberately does not grant a production isolation attestation by
itself. The application driver additionally checks the actual AWS supervisor
principal ARN and the live resource ownership/stage/fence tags; staging E2E-05
and E2E-06 still require execution and cleanup evidence in an authorized AWS
account.

`guest/bootstrap-control.sh` is the AMI hardening/bootstrap step. The AMI build
pipeline must record the concrete source AMI, Docker/Python package versions,
guest-agent digest, SSH host-key fingerprint, supervisor public key identity,
SBOM, and promoted AMI id in a reviewed image manifest. This checkout contains
the bootstrap logic, but does not name an approved base AMI or claim an AMI has
been built/promoted.
