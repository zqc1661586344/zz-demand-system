"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.database import init_db, SessionLocal
from app.models.user import User, Role
from app.services.auth_service import hash_password


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

# CORS — allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
def root():
    return {"message": "Enterprise RAG System", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    # 本地直跑（python app/main.py）用的端口，与项目约定的 8001 保持一致。
    # 生产部署请用 uvicorn/gunicorn 指定端口，不要走这个 reload 开发分支。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
