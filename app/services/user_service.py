"""User management service."""

from sqlalchemy.orm import Session

from app.models.user import Role, User
from app.services.auth_service import hash_password


def get_users(db: Session, skip: int = 0, limit: int = 100) -> list[User]:
    return db.query(User).offset(skip).limit(limit).all()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def update_user(db: Session, user_id: str, full_name: str | None = None, email: str | None = None, is_active: bool | None = None) -> User | None:
    user = get_user_by_id(db, user_id)
    if user is None:
        return None
    if full_name is not None:
        user.full_name = full_name
    if email is not None:
        user.email = email
    if is_active is not None:
        user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user_id: str) -> bool:
    user = get_user_by_id(db, user_id)
    if user is None:
        return False
    db.delete(user)
    db.commit()
    return True


def set_user_roles(db: Session, user_id: str, role_names: list[str]) -> User | None:
    """Replace a user's roles with the given list of role names."""
    user = get_user_by_id(db, user_id)
    if user is None:
        return None
    roles = db.query(Role).filter(Role.name.in_(role_names)).all()
    if len(roles) != len(role_names):
        raise ValueError(f"Invalid role names: {set(role_names) - {r.name for r in roles}}")
    user.roles = roles
    db.commit()
    db.refresh(user)
    return user