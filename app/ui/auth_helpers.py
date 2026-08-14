"""Auth helpers for Gradio — store/retrieve tokens from gr.State."""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AuthState:
    """Serialisable authentication state."""
    access_token: str = ""
    refresh_token: str = ""
    user_info: dict | None = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access_token) and self.user_info is not None

    @property
    def username(self) -> str:
        return (self.user_info or {}).get("username", "")

    @property
    def roles(self) -> list[str]:
        return (self.user_info or {}).get("roles", [])

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles or (self.user_info or {}).get("is_superuser", False)

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "user_info": self.user_info,
        })

    @classmethod
    def from_json(cls, data: str) -> "AuthState":
        try:
            obj = json.loads(data)
            return cls(
                access_token=obj.get("access_token", ""),
                refresh_token=obj.get("refresh_token", ""),
                user_info=obj.get("user_info"),
            )
        except (json.JSONDecodeError, TypeError):
            return cls()


def make_api_client(auth_state_json: str):
    """Create an ApiClient from the serialised auth state."""
    from app.ui.api_client import ApiClient

    state = AuthState.from_json(auth_state_json)
    return ApiClient(access_token=state.access_token, refresh_token=state.refresh_token)