"""Append an audit event for the caller (orders and tickets, ADR-029). Inside the tenant's session."""

import uuid
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.db.models import AuditEvent


def record(
    session: Session,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    details: Mapping[str, object] | None = None,
    request_id: str | None = None,
) -> None:
    actor = f"user:{principal.user_id}" if principal.user_id else "guest"
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            actor_label=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=dict(details or {}),
            request_id=request_id,
        )
    )
