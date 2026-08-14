"""Workflow service — definition and instance management."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowStep


def create_definition(db: Session, name: str, created_by: str, description: str | None = None, config: str | None = None) -> WorkflowDefinition:
    defn = WorkflowDefinition(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        config=config,
        created_by=created_by,
    )
    db.add(defn)
    db.commit()
    db.refresh(defn)
    return defn


def get_definitions(db: Session, skip: int = 0, limit: int = 100) -> list[WorkflowDefinition]:
    return db.query(WorkflowDefinition).offset(skip).limit(limit).all()


def get_definition_by_id(db: Session, def_id: str) -> WorkflowDefinition | None:
    return db.query(WorkflowDefinition).filter(WorkflowDefinition.id == def_id).first()


def create_instance(db: Session, definition_id: str, initiated_by: str, input_data: str | None = None) -> WorkflowInstance:
    inst = WorkflowInstance(
        id=str(uuid.uuid4()),
        definition_id=definition_id,
        status="pending",
        input_data=input_data,
        initiated_by=initiated_by,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def get_instances(db: Session, skip: int = 0, limit: int = 100) -> list[WorkflowInstance]:
    return db.query(WorkflowInstance).order_by(WorkflowInstance.created_at.desc()).offset(skip).limit(limit).all()


def get_instance_by_id(db: Session, inst_id: str) -> WorkflowInstance | None:
    return db.query(WorkflowInstance).filter(WorkflowInstance.id == inst_id).first()


def get_steps(db: Session, instance_id: str) -> list[WorkflowStep]:
    return db.query(WorkflowStep).filter(WorkflowStep.instance_id == instance_id).order_by(WorkflowStep.started_at.asc()).all()