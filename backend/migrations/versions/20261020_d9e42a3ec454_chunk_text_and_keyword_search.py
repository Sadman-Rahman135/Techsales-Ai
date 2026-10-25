"""chunk text and keyword search

Revision ID: d9e42a3ec454
Revises: 7138e1374249
Create Date: 2026-10-20 09:28:03.304078+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Reviewed (ADR-028): document_chunks keeps its RLS policy; the new columns need no policy of their own.
# Adding a stored generated column rewrites the table once (small: one row per chunk). Existing rows get
# text = NULL (an empty search vector) until `python -m app.ingestion.rebuild` fills them from the originals.
SEARCH_VECTOR = "to_tsvector('english', coalesce(section, '') || ' ' || coalesce(text, ''))"

revision: str = "d9e42a3ec454"
down_revision: str | Sequence[str] | None = "7138e1374249"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("text", sa.Text(), nullable=True))
    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks", postgresql_using="gin")
    op.drop_column("document_chunks", "search_vector")
    op.drop_column("document_chunks", "text")
