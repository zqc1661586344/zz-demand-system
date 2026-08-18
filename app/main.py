"""FastAPI application entry point."""

import os

# Allow Gradio file server to serve avatar SVGs from static directory
_STATIC_DIR = os.path.abspath("app/ui/static")
if os.path.isdir(_STATIC_DIR):
    existing = os.environ.get("GRADIO_ALLOWED_PATHS", "")
    parts = [p for p in existing.split(",") if p]
    if _STATIC_DIR not in parts:
        parts.append(_STATIC_DIR)
        os.environ["GRADIO_ALLOWED_PATHS"] = ",".join(parts)

from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.database import init_db, SessionLocal
from app.models.user import User, Role
from app.services.auth_service import hash_password
from app.ui.app import app as gradio_app


def _seed_demo_user():
    """Create a demo user for development/testing."""
    db: Session = SessionLocal()
    try:
        demo = db.query(User).filter(User.username == "admin").first()
        if demo:
            return
        viewer_role = db.query(Role).filter(Role.name == "admin").first()
        user = User(
            username="admin",
            email="admin@demo.com",
            full_name="Demo Admin",
            hashed_password=hash_password("admin123"),
            is_active=True,
            is_superuser=True,
        )
        if viewer_role:
            user.roles = [viewer_role]
        db.add(user)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initialize database on startup."""
    init_db()
    _seed_demo_user()
    yield


app = FastAPI(
    title="Enterprise RAG System",
    description="Internal knowledge base management with RAG-powered Q&A",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow Gradio frontend (same-origin or proxy)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router)

# Mount UI static files (avatars, etc.) — must be before Gradio mount
app.mount("/static", StaticFiles(directory="app/ui/static"), name="ui-static")

# Mount Gradio frontend at /ui
app = gr.mount_gradio_app(app, gradio_app, path="/ui")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
def root():
    return {"message": "Enterprise RAG System", "docs": "/docs", "ui": "/ui"}


if __name__ == "__main__":
    import uvicorn

    # Required for Gradio sub-app
    os.environ.setdefault("GRADIO_SERVER_NAME", "0.0.0.0")
    # Allow Gradio to serve avatar files via its /gradio_api/file= endpoint
    static_dir = os.path.abspath("app/ui/static")
    os.environ.setdefault("GRADIO_ALLOWED_PATHS", static_dir)

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
