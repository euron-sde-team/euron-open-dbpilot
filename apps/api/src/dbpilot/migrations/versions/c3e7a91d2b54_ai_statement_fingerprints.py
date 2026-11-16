"""AI statement fingerprints: one per generated statement, not only the whole output

The write path refuses a statement the AI assistant generated. Matching only the whole
output's fingerprint let a person lift one statement out of a refused multi-statement
answer and submit it as a write; every statement's own fingerprint is kept now.

Revision ID: c3e7a91d2b54
Revises: a54d19f4588b
Create Date: 2026-11-13 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e7a91d2b54"
down_revision: str | Sequence[str] | None = "a54d19f4588b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_generations",
        sa.Column(
            "statement_fingerprints",
            postgresql.ARRAY(sa.String(length=64)),
            server_default="{}",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE ai_generations SET statement_fingerprints = ARRAY[sql_fingerprint] "
        "WHERE sql_fingerprint IS NOT NULL"
    )
    op.create_index(
        "ix_ai_generations_statement_fingerprints",
        "ai_generations",
        ["statement_fingerprints"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_index("ix_ai_generations_project_fingerprint", table_name="ai_generations")


def downgrade() -> None:
    op.create_index(
        "ix_ai_generations_project_fingerprint",
        "ai_generations",
        ["project_id", "sql_fingerprint"],
        unique=False,
    )
    op.drop_index(
        "ix_ai_generations_statement_fingerprints",
        table_name="ai_generations",
        postgresql_using="gin",
    )
    op.drop_column("ai_generations", "statement_fingerprints")
