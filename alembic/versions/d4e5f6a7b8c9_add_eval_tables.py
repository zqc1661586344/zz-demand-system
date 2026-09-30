"""add golden_datasets and eval_runs tables for automated regression eval

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6g7h8
Create Date: 2026-09-30

Changes (powered by docs/review_comments.md Round Fast fixes):
  1. golden_datasets  CREATE TABLE IF NOT EXISTS — 固定评估集（RAGAS golden）
  2. eval_runs        CREATE TABLE IF NOT EXISTS — 评估历史快照 + 回归基线
  3. complements the dev-only Base.metadata.create_all in app/database.py

All statements use IF NOT EXISTS so the migration is **idempotent** and safe
to run on DBs where these tables were already created by create_all.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS golden_datasets (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                question TEXT NOT NULL,
                ground_truth TEXT NOT NULL,
                domain VARCHAR(100),
                tags TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS eval_runs (
                id VARCHAR(36) PRIMARY KEY,
                trigger VARCHAR(20),
                scene VARCHAR(20),
                dataset_name VARCHAR(255),
                params TEXT,
                metric_values TEXT,
                status VARCHAR(20),
                notes TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS eval_runs"))
    op.execute(sa.text("DROP TABLE IF EXISTS golden_datasets"))
