"""Workflow Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel


class WorkflowDefinitionCreate(BaseModel):
    name: str
    description: str | None = None
    config: str | None = None  # JSON string


class WorkflowDefinitionResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    config: str | None = None
    version: int
    is_active: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowInstanceCreate(BaseModel):
    definition_id: str
    input_data: str | None = None  # JSON string


class WorkflowInstanceResponse(BaseModel):
    id: str
    definition_id: str
    status: str
    input_data: str | None = None
    output_data: str | None = None
    initiated_by: str
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class WorkflowStepResponse(BaseModel):
    id: str
    instance_id: str
    step_name: str
    status: str
    input_data: str | None = None
    output_data: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}