"""add compliance_human_actions.note column

Revision ID: c3d4e5f6g7h8
Revises: b2d3c4e5f6a7
Create Date: 2026-09-10
Changes (powered by docs/review_comments.md Round 7 fixes):
  1. compliance_human_actions ADD note TEXT NULL
     — 人工审核操作的备注说明字段，之前手动改 DB 加了列但漏了 Alembic 迁移脚本。
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, Sequence[str], None] = "b2d3c4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE compliance_human_actions ADD COLUMN IF NOT EXISTS note TEXT"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE compliance_human_actions DROP COLUMN IF EXISTS note"))
