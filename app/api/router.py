"""Aggregate API router — mount all sub-routers."""

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.documents import router as documents_router
from app.api.conversations import router as conversations_router
from app.api.workflows import router as workflows_router
from app.compliance.api.reviews import router as compliance_reviews_router
from app.compliance.api.playbooks import router as compliance_playbooks_router
from app.compliance.api.knowledge import router as compliance_knowledge_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(documents_router)
api_router.include_router(conversations_router)
api_router.include_router(workflows_router)
api_router.include_router(compliance_reviews_router)
api_router.include_router(compliance_playbooks_router)
api_router.include_router(compliance_knowledge_router)
