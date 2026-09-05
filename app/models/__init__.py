"""ORM models package."""

from app.database import Base

# Import all models so Alembic can discover them
from app.models.user import User, Role, UserRole
from app.models.document import Document, DocumentChunk
from app.models.conversation import Conversation, Message
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowStep

# 合规审查模块模型：受 COMPLIANCE_ENABLED 门控。
# 注册到 Base.metadata 后，init_db() 的 create_all 会自动建表（compliance_* 前缀），
# Alembic autogenerate 也能发现它们。
from app.config import settings

if settings.compliance_enabled:
    import app.compliance.models  # noqa: F401 —— 仅注册到 Base.metadata

__all__ = [
    "Base",
    "User",
    "Role",
    "UserRole",
    "Document",
    "DocumentChunk",
    "Conversation",
    "Message",
    "WorkflowDefinition",
    "WorkflowInstance",
    "WorkflowStep",
]