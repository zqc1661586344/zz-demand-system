"""Workflow ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class WorkflowDefinition(Base):
    """
    工作流定义类，用于存储工作流的基本信息和配置。
    继承自Base类，使用SQLAlchemy ORM映射到数据库表。
    """

    __tablename__ = "workflow_definitions"  # 指定对应的数据库表名

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    config = Column(Text, nullable=True)  # JSON configuration
    version = Column(Integer, default=1)
    is_active = Column(String(5), default="true")  # "true" / "false"
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    creator = relationship("User", back_populates="workflow_definitions")
    instances = relationship(
        "WorkflowInstance", back_populates="definition", cascade="all, delete-orphan"
    )


class WorkflowInstance(Base):
    """
    工作流实例类，用于表示工作流的一个具体执行实例。
    继承自Base类，使用SQLAlchemy ORM映射到数据库表workflow_instances。
    """

    __tablename__ = "workflow_instances"

    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # 实例ID，使用UUID生成，确保唯一性
    definition_id = Column(
        String(36), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False
    )  # 关联的工作流定义ID，外键关联，级联删除
    status = Column(
        String(50), default="pending", index=True
    )  # pending, running, completed, failed
    input_data = Column(Text, nullable=True)  # JSON
    output_data = Column(Text, nullable=True)  # JSON
    initiated_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    definition = relationship("WorkflowDefinition", back_populates="instances")
    initiator = relationship("User", back_populates="workflow_instances")
    steps = relationship("WorkflowStep", back_populates="instance", cascade="all, delete-orphan")


class WorkflowStep(Base):
    """
    工作流步骤类，用于表示工作流中的单个步骤。
    继承自Base类，使用SQLAlchemy ORM进行数据库映射。
    """

    __tablename__ = "workflow_steps"  # 指定数据库表名为workflow_steps

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    instance_id = Column(
        String(36), ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False
    )
    step_name = Column(String(255), nullable=False)
    status = Column(String(50), default="pending")  # pending, running, completed, failed
    input_data = Column(Text, nullable=True)  # JSON
    output_data = Column(Text, nullable=True)  # JSON
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    instance = relationship("WorkflowInstance", back_populates="steps")
