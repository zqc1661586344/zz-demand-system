"""add_compliance_tables

Revision ID: a1c0mp1i4nce
Revises: 012acc6c2410
Create Date: 2026-09-01

创建文档合规审查模块（app/compliance/）的全部 compliance_* 表（11 张）。
与 ORM 模型 app/compliance/models/* 保持一致；仅供已有 PG 库补充表结构用，
主路径是 init_db() 的 Base.metadata.create_all（SQLite/PG 均可自动建表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c0mp1i4nce"
down_revision: Union[str, Sequence[str], None] = "012acc6c2410"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) 合规审查文档
    op.create_table(
        "compliance_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("doc_type", sa.String(length=50), nullable=True),
        sa.Column("doc_type_confidence", sa.Float(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_documents_document_id", "compliance_documents", ["document_id"]
    )
    op.create_index("ix_compliance_documents_status", "compliance_documents", ["status"])

    # 2) 合同条款（审查最小单元）
    op.create_table(
        "compliance_clauses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("compliance_doc_id", sa.String(length=36), nullable=False),
        sa.Column("clause_number", sa.String(length=50), nullable=True),
        sa.Column("clause_type", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["compliance_doc_id"], ["compliance_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_clauses_compliance_doc_id", "compliance_clauses", ["compliance_doc_id"]
    )

    # 3) 合同关键信息
    op.create_table(
        "compliance_key_info",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("compliance_doc_id", sa.String(length=36), nullable=False),
        sa.Column("field_key", sa.String(length=50), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("clause_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["clause_id"], ["compliance_clauses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["compliance_doc_id"], ["compliance_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_key_info_compliance_doc_id", "compliance_key_info", ["compliance_doc_id"]
    )

    # 4) 审查任务
    op.create_table(
        "compliance_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("compliance_doc_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("high_risk_count", sa.Integer(), nullable=True),
        sa.Column("medium_risk_count", sa.Integer(), nullable=True),
        sa.Column("low_risk_count", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("template_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["compliance_doc_id"], ["compliance_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_reviews_compliance_doc_id", "compliance_reviews", ["compliance_doc_id"]
    )
    op.create_index("ix_compliance_reviews_status", "compliance_reviews", ["status"])
    op.create_index("ix_compliance_reviews_thread_id", "compliance_reviews", ["thread_id"])

    # 5) 风险项
    op.create_table(
        "compliance_risks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("clause_id", sa.String(length=36), nullable=True),
        sa.Column("risk_level", sa.String(length=10), nullable=False),
        sa.Column("risk_category", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("suggestion_reason", sa.Text(), nullable=True),
        sa.Column("playbook_rule_id", sa.String(length=36), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("human_confirmed", sa.Boolean(), nullable=True),
        sa.Column("human_decision", sa.String(length=20), nullable=True),
        sa.Column("human_note", sa.Text(), nullable=True),
        sa.Column("human_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("human_reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["clause_id"], ["compliance_clauses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["human_reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["review_id"], ["compliance_reviews.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_risks_review_id", "compliance_risks", ["review_id"])
    op.create_index("ix_compliance_risks_risk_level", "compliance_risks", ["risk_level"])

    # 6) 风险项引用（法规/判例/Playbook）
    op.create_table(
        "compliance_risk_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("risk_id", sa.String(length=36), nullable=False),
        sa.Column("ref_type", sa.String(length=20), nullable=False),
        sa.Column("ref_name", sa.String(length=500), nullable=False),
        sa.Column("ref_article", sa.String(length=100), nullable=True),
        sa.Column("ref_content", sa.Text(), nullable=False),
        sa.Column("ref_source_url", sa.String(length=1000), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["risk_id"], ["compliance_risks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_risk_references_risk_id", "compliance_risk_references", ["risk_id"])

    # 7) 法规库
    op.create_table(
        "compliance_regulations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("regulation_type", sa.String(length=30), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expire_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 8) 法规条款（结构化元数据，向量由独立 PGVector collection 承载）
    op.create_table(
        "compliance_regulation_articles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("regulation_id", sa.String(length=36), nullable=False),
        sa.Column("article_number", sa.String(length=50), nullable=False),
        sa.Column("chapter", sa.String(length=200), nullable=True),
        sa.Column("section", sa.String(length=200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["regulation_id"], ["compliance_regulations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_regulation_articles_regulation_id",
        "compliance_regulation_articles",
        ["regulation_id"],
    )

    # 9) Playbook 审查规则
    op.create_table(
        "compliance_playbooks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contract_type", sa.String(length=50), nullable=False),
        sa.Column("clause_type", sa.String(length=50), nullable=True),
        sa.Column("risk_level", sa.String(length=10), nullable=False),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("match_pattern", sa.Text(), nullable=True),
        sa.Column("match_threshold", sa.Float(), nullable=True),
        sa.Column("legal_basis_ref", sa.Text(), nullable=True),
        sa.Column("standard_position", sa.Text(), nullable=True),
        sa.Column("red_line", sa.Boolean(), nullable=True),
        sa.Column("negotiable", sa.Boolean(), nullable=True),
        sa.Column("suggested_clause", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_playbooks_contract_type", "compliance_playbooks", ["contract_type"])

    # 10) 人工审核操作日志
    op.create_table(
        "compliance_human_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("risk_id", sa.String(length=36), nullable=True),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("operator_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["review_id"], ["compliance_reviews.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["risk_id"], ["compliance_risks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_human_actions_review_id", "compliance_human_actions", ["review_id"])

    # 11) 审查报告
    op.create_table(
        "compliance_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["review_id"], ["compliance_reviews.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_reports_review_id", "compliance_reports", ["review_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_compliance_reports_review_id", table_name="compliance_reports")
    op.drop_table("compliance_reports")
    op.drop_index("ix_compliance_human_actions_review_id", table_name="compliance_human_actions")
    op.drop_table("compliance_human_actions")
    op.drop_index("ix_compliance_playbooks_contract_type", table_name="compliance_playbooks")
    op.drop_table("compliance_playbooks")
    op.drop_index(
        "ix_compliance_regulation_articles_regulation_id",
        table_name="compliance_regulation_articles",
    )
    op.drop_table("compliance_regulation_articles")
    op.drop_table("compliance_regulations")
    op.drop_index("ix_compliance_risk_references_risk_id", table_name="compliance_risk_references")
    op.drop_table("compliance_risk_references")
    op.drop_index("ix_compliance_risks_risk_level", table_name="compliance_risks")
    op.drop_index("ix_compliance_risks_review_id", table_name="compliance_risks")
    op.drop_table("compliance_risks")
    op.drop_index("ix_compliance_reviews_thread_id", table_name="compliance_reviews")
    op.drop_index("ix_compliance_reviews_status", table_name="compliance_reviews")
    op.drop_index("ix_compliance_reviews_compliance_doc_id", table_name="compliance_reviews")
    op.drop_table("compliance_reviews")
    op.drop_index("ix_compliance_key_info_compliance_doc_id", table_name="compliance_key_info")
    op.drop_table("compliance_key_info")
    op.drop_index("ix_compliance_clauses_compliance_doc_id", table_name="compliance_clauses")
    op.drop_table("compliance_clauses")
    op.drop_index("ix_compliance_documents_status", table_name="compliance_documents")
    op.drop_index("ix_compliance_documents_document_id", table_name="compliance_documents")
    op.drop_table("compliance_documents")