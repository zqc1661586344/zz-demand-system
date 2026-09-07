"""Database engine and session configuration."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_db_url = settings.database_url or "sqlite:///./data/app.db"
engine = create_engine(
    _db_url,
    echo=False,
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


if "sqlite" in _db_url:

    @event.listens_for(engine, "connect")
    def _set_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


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
    """Create default roles (admin, legal, editor, viewer) if they don't exist.

    legal 角色用于合规审查的人工审核（human-review / resume 端点）。
    """
    from app.models.user import Role

    db = SessionLocal()
    try:
        existing = {r.name for r in db.query(Role).all()}
        default_roles = [
            Role(name="admin", description="Administrator — full system access"),
            Role(name="legal", description="Legal — compliance review, HITL operations"),
            Role(name="editor", description="Editor — can manage documents and workflows"),
            Role(name="viewer", description="Viewer — read-only access"),
        ]
        for role in default_roles:
            if role.name not in existing:
                db.add(role)
        db.commit()
    finally:
        db.close()
