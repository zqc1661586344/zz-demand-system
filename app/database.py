"""Database engine and session configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    echo=settings.app_debug,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
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