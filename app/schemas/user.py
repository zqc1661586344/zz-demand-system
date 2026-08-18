"""User management Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    roles: list[str] = []

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_roles(cls, user: User) -> UserResponse:
        """从一个 User ORM 对象构造 UserResponse，提取出角色名称列表。"""
        role_names = [r.name for r in user.roles] if user.roles else []
        # 用 dict 中转避免 Pylance Column[T] → T 的类型误报
        data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "created_at": user.created_at,
            "roles": role_names,
        }
        return cls(**data)


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None


class UserRoleUpdate(BaseModel):
    role_names: list[str]
