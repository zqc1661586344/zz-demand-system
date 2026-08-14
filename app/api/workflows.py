"""Workflow API routes — definitions and instances."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import User
from app.schemas.workflow import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionResponse,
    WorkflowInstanceCreate,
    WorkflowInstanceResponse,
    WorkflowStepResponse,
)
from app.services.workflow_service import (
    create_definition,
    create_instance,
    get_definition_by_id,
    get_definitions,
    get_instance_by_id,
    get_instances,
    get_steps,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.post("/definitions", response_model=WorkflowDefinitionResponse, status_code=201)
def create_workflow_definition(
    req: WorkflowDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    return create_definition(
        db,
        name=req.name,
        description=req.description,
        config=req.config,
        created_by=current_user.id,
    )


@router.get("/definitions", response_model=list[WorkflowDefinitionResponse])
def list_workflow_definitions(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_definitions(db, skip=skip, limit=limit)


@router.get("/definitions/{def_id}", response_model=WorkflowDefinitionResponse)
def get_workflow_definition(
    def_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    defn = get_definition_by_id(db, def_id)
    if defn is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    return defn


@router.post("/instances", response_model=WorkflowInstanceResponse, status_code=201)
def create_workflow_instance(
    req: WorkflowInstanceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    defn = get_definition_by_id(db, req.definition_id)
    if defn is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    return create_instance(db, definition_id=req.definition_id, initiated_by=current_user.id, input_data=req.input_data)


@router.get("/instances", response_model=list[WorkflowInstanceResponse])
def list_workflow_instances(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_instances(db, skip=skip, limit=limit)


@router.get("/instances/{inst_id}", response_model=WorkflowInstanceResponse)
def get_workflow_instance(
    inst_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst = get_instance_by_id(db, inst_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return inst


@router.get("/instances/{inst_id}/steps", response_model=list[WorkflowStepResponse])
def list_workflow_steps(
    inst_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst = get_instance_by_id(db, inst_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return get_steps(db, inst_id)