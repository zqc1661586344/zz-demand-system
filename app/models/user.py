"""User and Role ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Role(Base):
    """
    角色模型类，用于定义系统中不同的角色及其属性。
    继承自Base类，通常用于数据库ORM映射。
    """

    __tablename__ = "roles"  # 指定数据库中的表名为"roles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(50), unique=True, nullable=False, index=True)  # admin, editor, viewer
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    users = relationship("User", secondary="user_roles", back_populates="roles")


class User(Base):
    """
    用户模型类，继承自Base类，用于定义用户表的结构和属性。
    使用SQLAlchemy ORM进行数据库映射。
    """

    __tablename__ = "users"  # 指定数据库表名为"users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    roles = relationship("Role", secondary="user_roles", back_populates="users")

    # Relationships
    documents = relationship("Document", back_populates="uploader")
    conversations = relationship("Conversation", back_populates="creator")
    workflow_definitions = relationship("WorkflowDefinition", back_populates="creator")
    workflow_instances = relationship("WorkflowInstance", back_populates="initiator")


class UserRole(Base):
    """
    用户角色关联表模型类
    用于存储用户与角色之间的多对多关系
    """

    __tablename__ = "user_roles"  # 指定数据库表名为"user_roles"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
