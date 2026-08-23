"""Auth service — password hashing, JWT creation/refresh."""

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
import bcrypt as _bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import Role, User


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    """生成一个短效的JWT访问令牌。"""
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_access_expire_seconds)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    """创建一个有效期更长的JWT刷新令牌。"""
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_refresh_expire_seconds)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> str | None:
    """验证刷新令牌并返回用户ID。若验证失败，则返回None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            return None
        return payload.get("sub")
    except JWTError:
        return None


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """验证凭据并返回用户，若验证失败则返回None。"""
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def register_user(
    db: Session,
    username: str,
    password: str,
    email: str,
    full_name: str | None = None,
) -> User:
    """创建一个具有默认“查看者”角色的新用户。"""
    viewer_role = db.query(Role).filter(Role.name == "viewer").first()

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        is_active=True,
        is_superuser=False,
    )

    if viewer_role:
        user.roles.append(viewer_role)

    db.add(user)
    db.commit()
    db.refresh(user)
    return user
