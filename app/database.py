"""Database engine and session configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_db_url = settings.database_url or "sqlite:///./data/app.db"
engine = create_engine(
    _db_url,
    # echo 交由 logging_config 统一控制（_configure_logging 已将 sqlalchemy.* 设为 WARNING）
    # 避免 Engine 在每次执行 SQL 时重置 logger 级别覆盖我们的配置
    echo=False,
    # SQLite：check_same_thread=False 允许多线程读
    # PostgreSQL：连接池配置
    **(
        {"connect_args": {"check_same_thread": False}}
        if "sqlite" in _db_url
        else {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_pre_ping": settings.db_pool_pre_ping,
            "pool_recycle": settings.db_pool_recycle,
        }
    ),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and seed default data. Called on startup."""
    Base.metadata.create_all(bind=engine)
    _seed_roles()


def _seed_roles() -> None:
    """Create default roles (admin, editor, viewer) if they don't exist."""
    from app.models.user import Role

    db = SessionLocal()
    try:
        existing = {r.name for r in db.query(Role).all()}
        default_roles = [
            Role(name="admin", description="Administrator — full system access"),
            Role(name="editor", description="Editor — can manage documents and workflows"),
            Role(name="viewer", description="Viewer — read-only access"),
        ]
        for role in default_roles:
            if role.name not in existing:
                db.add(role)
        db.commit()
    finally:
        db.close()