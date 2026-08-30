"""Auth-related Pydantic schemas."""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    email: str = Field("", max_length=128)
    full_name: str | None = Field(None, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: str
    username: str
    email: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    roles: list[str] = []

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_roles(cls, user) -> "UserInfo":
        role_names = [r.name for r in user.roles] if user.roles else []
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            roles=role_names,
        )


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
