"""Auth helpers for Streamlit — login, register, logout, and token management.

替代旧的 auto_login（硬编码 admin/admin123）：
- 凭据由登录/注册表单录入，不再写死超管账号。
- 所有 token 仅存 st.session_state（刷新需重登，不落地）。
- ApiClient（api_client.py）读取同一套 session 字段自动注入 Bearer 并在 401 时 refresh，
  这里不重复实现网络层。
"""

import httpx
import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import BASE_URL

logger = get_logger(__name__)

# session_state 中受认证管控的键，login/logout 共同维护
_AUTH_KEYS = ("access_token", "refresh_token", "user_info", "username", "authenticated")


def _set_auth_session(data: dict, user_info: dict | None = None) -> None:
    """把登录成功后的令牌与用户信息写入 session_state。"""
    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    st.session_state["user_info"] = user_info or {}
    st.session_state["username"] = (user_info or {}).get("username", "")
    st.session_state["authenticated"] = True


def _fetch_me(token: str) -> dict:
    """用 access token 拉取当前用户信息（/api/auth/me）。

    失败（非 200、JSON 解析失败、网络异常）返回空 dict，**不向上抛异常**，
    避免让一次本该成功的登录/注册因"拉 user_info"这个小步骤失败而被误判为整体失败。
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.info("fetch /me failed: status=%s", resp.status_code)
                return {}
            return resp.json()
    except Exception as e:
        # 网络异常 / JSON 解析失败：仅记录，不阻断登录
        logger.warning("fetch /me failed: %s", e)
        return {}


def login(username: str, password: str) -> bool:
    """用户名密码登录；成功写会话态返回 True，凭据错误返回 False。"""
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            if resp.status_code != 200:
                logger.info("login failed for %r: %s", username, resp.status_code)
                return False
            data = resp.json()
            user_info = _fetch_me(data["access_token"])
        _set_auth_session(data, user_info)
        logger.info("user logged in: %s", username)
        return True
    except Exception as e:
        logger.error("login exception: %s", e)
        return False


def register(username: str, password: str, email: str = "", full_name: str = "") -> bool:
    """注册新用户；成功后自动登录并写会话态。

    后端 /api/auth/register 返回 TokenResponse（无 user_info），故写态后补一次 /me。
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "password": password,
                    "email": email,
                    "full_name": full_name,
                },
            )
            if resp.status_code != 201:
                # 透出后端 detail（如 "Username already exists"）供前端展示
                try:
                    raw = resp.json().get("detail", resp.text)
                    detail = raw if isinstance(raw, str) else str(raw)  # 422 的 detail 是列表
                except Exception:
                    detail = resp.text
                st.session_state["register_error"] = detail
                logger.info("register failed for %r: %s %s", username, resp.status_code, detail)
                return False
            data = resp.json()
            user_info = _fetch_me(data["access_token"])
        _set_auth_session(data, user_info)
        logger.info("user registered & logged in: %s", username)
        return True
    except Exception as e:
        logger.error("register exception: %s", e)
        st.session_state["register_error"] = "注册失败，请稍后再试"
        return False


def logout() -> None:
    """清空会话态，前端调用后应 st.rerun()。"""
    for key in _AUTH_KEYS:
        st.session_state.pop(key, None)
    st.session_state.pop("register_error", None)
    logger.info("user logged out")
