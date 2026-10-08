"""Object-level authorization for private benchmark-audit documents."""

from __future__ import annotations

from polycodebench_core.benchmark_audit_documents import AuditDocument

from polycodebench_api.auth import ApiPrincipal


class OwnerAuditAccessPolicy:
    """Allow an authenticated tenant principal to access only documents they authored.

    Shared reviewer access must be supplied by an installation-specific ACL. Audit-document
    rows do not carry a tenant column, so this default never grants cross-owner access.
    """

    def allows(
        self,
        *,
        principal: ApiPrincipal,
        document: AuditDocument,
        action: str,
    ) -> bool:
        return (
            principal.tenant_id is not None
            and action in {"read", "write", "run"}
            and document.metadata.actor == principal.subject_id
        )


__all__ = ["OwnerAuditAccessPolicy"]
