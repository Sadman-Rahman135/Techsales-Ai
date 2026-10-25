"""Who is asking, and what they may see (docs/product-vision.md §3 permission matrix, ADR-004)."""

import uuid
from dataclasses import dataclass

from app.db.models import Audience, Role

STAFF_ROLES = frozenset({Role.ADMIN, Role.MANAGER, Role.EMPLOYEE})
DOCUMENT_MANAGER_ROLES = frozenset({Role.ADMIN, Role.MANAGER})


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved server-side from the session or service credential, never from request fields."""

    organization_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: Role | None = None  # None = anonymous customer

    @property
    def allowed_audiences(self) -> tuple[Audience, ...]:
        if self.role in STAFF_ROLES:
            return (Audience.PUBLIC, Audience.EMPLOYEE)
        return (Audience.PUBLIC,)

    @property
    def can_manage_documents(self) -> bool:
        return self.role in DOCUMENT_MANAGER_ROLES

    @property
    def is_staff(self) -> bool:
        """Employees, managers and admins: the support ticket queue (ADR-029)."""
        return self.role in STAFF_ROLES
