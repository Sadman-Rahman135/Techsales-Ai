"""document metadata for search

Revision ID: 6f67937742dd
Revises: d9e42a3ec454
Create Date: 2026-10-20 09:49:07.385099+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Reviewed: autogenerate skips CHECK constraints on existing tables, so ck_documents_language_code is added by
# hand. Existing documents get language 'en' (the only language so far, §2.2); the other fields stay empty
# until a manager fills them in. No RLS change: the columns belong to `documents`, already tenant-scoped.

revision: str = "6f67937742dd"
down_revision: str | Sequence[str] | None = "d9e42a3ec454"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("brand", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("department", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("language", sa.Text(), server_default="en", nullable=False))
    op.add_column("documents", sa.Column("effective_date", sa.Date(), nullable=True))
    op.create_check_constraint("language_code", "documents", "language ~ '^[a-z]{2}$'")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_language_code"), "documents", type_="check")
    op.drop_column("documents", "effective_date")
    op.drop_column("documents", "language")
    op.drop_column("documents", "department")
    op.drop_column("documents", "brand")
