"""User management Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel


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
    def from_orm_with_roles(cls, user: object) -> "UserResponse":
        """Construct from a User ORM object, extracting role names."""
        role_names = [r.name for r in user.roles] if user.roles else []
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            created_at=user.created_at,
            roles=role_names,
        )


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None


class UserRoleUpdate(BaseModel):
    role_names: list[str]