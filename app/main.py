"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.config import settings
from app.database import init_db, SessionLocal
from app.logging_config import _configure_logging
from app.middleware.tracing import TracingMiddleware
from app.models.user import User, Role
from app.services.auth_service import hash_password


def _seed_demo_user():
    """Create a demo user for development/testing. 生产环境不创建。"""
    if settings.environment == "production":
        return
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
    """应用程序生命周期：在启动时初始化日志记录和数据库，并恢复卡住的文档。"""
    _configure_logging()
    init_db()
    _seed_demo_user()
    # 为 document_chunks.search_text 建 PG tsvector GIN 索引（仅 PG，幂等）
    from app.rag.sparse_search import ensure_fts_index

    ensure_fts_index()
    # 恢复因异常退出而卡在 "processing" 状态的文档
    _recover_stuck_documents()
    yield


def _recover_stuck_documents() -> None:
    """启动时将 status == 'processing' 的文档重置为 'pending'（可重新处理）。"""
    from app.models.document import Document

    db = SessionLocal()
    try:
        stuck = db.query(Document).filter(Document.status == "processing").all()
        if stuck:
            for doc in stuck:
                doc.status = "pending"
            db.commit()
            import logging

            logging.getLogger(__name__).warning(
                "Reset %d stuck document(s) from 'processing' to 'pending'",
                len(stuck),
            )
    finally:
        db.close()


app = FastAPI(
    title="Enterprise RAG System",
    description="Internal knowledge base management with RAG-powered Q&A",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow all origins for development（生产环境用 CORS_ORIGINS 环境变量覆盖）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
if settings.rate_limit_enabled:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.middleware import SlowAPIMiddleware

    from app.middleware.rate_limit import get_limiter

    limiter = get_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(429, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

# Request tracing（注入 request_id）
app.add_middleware(TracingMiddleware)

# Mount API routes
app.include_router(api_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/health/live")
def liveness():
    """存活检查 — 仅返回服务是否在运行。"""
    return {"status": "alive"}


@app.get("/api/health/ready")
def readiness():
    """就绪检查 — pgvector 等外部依赖是否可用。"""
    from sqlalchemy import text

    deps = {}
    all_healthy = True

    # 1. Database
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        deps["database"] = "ok"
    except Exception as e:
        deps["database"] = f"error: {e}"
        all_healthy = False

    # 2. PGVector 向量库
    try:
        from app.rag.vector_store import _maintenance_engine

        with _maintenance_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        deps["vector_store"] = "ok"
    except Exception as e:
        deps["vector_store"] = f"error: {e}"
        all_healthy = False

    status_code = 200 if all_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "healthy" if all_healthy else "unhealthy", "dependencies": deps},
    )


@app.get("/")
def root():
    return {"message": "Enterprise RAG System", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    # 本地直跑（python app/main.py）用的端口，与项目约定的 8001 保持一致。
    # 生产部署请用 uvicorn/gunicorn 指定端口，不要走这个 reload 开发分支。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
