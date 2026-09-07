"""add_review_id + FK fixes for compliance tables

Revision ID: b2d3c4e5f6a7
Revises: a1c0mp1i4nce
Create Date: 2026-09-07

Changes (powered by docs/review_comments.md Round 2 fixes):
  1. compliance_clauses  ADD review_id + FK + index  —  同一文档多轮审查隔离
  2. compliance_key_info ADD review_id + FK + index  —  同上
  3. compliance_human_actions.risk_id FK: DROP old (RESTRICT) -> ADD new (ON DELETE SET NULL)
     —  批量删 risk 时不再 IntegrityError

All statements use IF NOT EXISTS / IF EXISTS so the migration is **idempotent**
and safe to run on DBs that were already patched manually.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2d3c4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1c0mp1i4nce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply schema additions."""

    # ── 1) compliance_clauses.review_id ──
    op.execute(
        sa.text("ALTER TABLE compliance_clauses ADD COLUMN IF NOT EXISTS review_id VARCHAR(36)")
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_clauses "
            "DROP CONSTRAINT IF EXISTS compliance_clauses_review_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_clauses "
            "ADD CONSTRAINT compliance_clauses_review_id_fkey "
            "FOREIGN KEY (review_id) REFERENCES compliance_reviews(id) ON DELETE CASCADE"
        )
    )
    op.create_index(
        op.f("ix_compliance_clauses_review_id"),
        "compliance_clauses",
        ["review_id"],
        unique=False,
        if_not_exists=True,
    )

    # ── 2) compliance_key_info.review_id ──
    op.execute(
        sa.text("ALTER TABLE compliance_key_info ADD COLUMN IF NOT EXISTS review_id VARCHAR(36)")
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_key_info "
            "DROP CONSTRAINT IF EXISTS compliance_key_info_review_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_key_info "
            "ADD CONSTRAINT compliance_key_info_review_id_fkey "
            "FOREIGN KEY (review_id) REFERENCES compliance_reviews(id) ON DELETE CASCADE"
        )
    )
    op.create_index(
        op.f("ix_compliance_key_info_review_id"),
        "compliance_key_info",
        ["review_id"],
        unique=False,
        if_not_exists=True,
    )

    # ── 3) compliance_human_actions.risk_id FK: RESTRICT -> ON DELETE SET NULL ──
    op.execute(
        sa.text(
            "ALTER TABLE compliance_human_actions "
            "DROP CONSTRAINT IF EXISTS compliance_human_actions_risk_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_human_actions "
            "ADD CONSTRAINT compliance_human_actions_risk_id_fkey "
            "FOREIGN KEY (risk_id) REFERENCES compliance_risks(id) ON DELETE SET NULL"
        )
    )


def downgrade() -> None:
    """Revert schema additions."""

    # 3) restore RESTRICT FK
    op.execute(
        sa.text(
            "ALTER TABLE compliance_human_actions "
            "DROP CONSTRAINT IF EXISTS compliance_human_actions_risk_id_fkey"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_human_actions "
            "ADD CONSTRAINT compliance_human_actions_risk_id_fkey "
            "FOREIGN KEY (risk_id) REFERENCES compliance_risks(id)"
        )
    )

    # 2) remove compliance_key_info.review_id
    op.drop_index(
        op.f("ix_compliance_key_info_review_id"),
        table_name="compliance_key_info",
        if_exists=True,
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_key_info "
            "DROP CONSTRAINT IF EXISTS compliance_key_info_review_id_fkey"
        )
    )
    op.execute(sa.text("ALTER TABLE compliance_key_info DROP COLUMN IF EXISTS review_id"))

    # 1) remove compliance_clauses.review_id
    op.drop_index(
        op.f("ix_compliance_clauses_review_id"),
        table_name="compliance_clauses",
        if_exists=True,
    )
    op.execute(
        sa.text(
            "ALTER TABLE compliance_clauses "
            "DROP CONSTRAINT IF EXISTS compliance_clauses_review_id_fkey"
        )
    )
    op.execute(sa.text("ALTER TABLE compliance_clauses DROP COLUMN IF EXISTS review_id"))
