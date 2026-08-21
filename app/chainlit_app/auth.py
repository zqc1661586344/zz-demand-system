"""Auth helpers for Chainlit — auto-login and token management."""

import logging

import httpx

from app.chainlit_app.api_client import BASE_URL

logger = logging.getLogger(__name__)


async def auto_login():
    """Auto-login with admin/admin123 and store tokens in Chainlit user session.

    Must be called from @cl.on_chat_start (or any async Chainlit handler).
    Tokens are stored in cl.user_session dict for later use by ApiClient.
    """
    import chainlit as cl

    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        resp = await http.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        if resp.status_code != 200:
            await cl.ErrorMessage(
                content=f"自动登录失败（{resp.status_code}），请检查后端服务。"
            ).send()
            return

        data = resp.json()
        me_resp = await http.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        user_info = (
            me_resp.json()
            if me_resp.status_code == 200
            else {"username": "admin", "roles": ["admin"]}
        )

    cl.user_session.set("access_token", data["access_token"])
    cl.user_session.set("refresh_token", data["refresh_token"])
    cl.user_session.set("user_info", user_info)
    cl.user_session.set("username", user_info.get("username", "admin"))


async def get_api_client():
    """Create an authenticated ApiClient from the current user session tokens."""
    import chainlit as cl

    from app.chainlit_app.api_client import ApiClient

    access_token = cl.user_session.get("access_token")
    refresh_token = cl.user_session.get("refresh_token")
    return ApiClient(access_token=access_token, refresh_token=refresh_token)