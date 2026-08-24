"""Auth helpers for Streamlit — auto-login and token management."""

import httpx
import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import BASE_URL

logger = get_logger(__name__)


def auto_login() -> bool:
    """Auto-login with admin/admin123 and store tokens in session state.

    Returns True if login succeeded.
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin123"},
            )
            if resp.status_code != 200:
                logger.error("Auto-login failed: %s %s", resp.status_code, resp.text)
                return False

            data = resp.json()
            me_resp = http.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
            user_info = (
                me_resp.json()
                if me_resp.status_code == 200
                else {"username": "admin", "roles": ["admin"]}
            )

        st.session_state["access_token"] = data["access_token"]
        st.session_state["refresh_token"] = data["refresh_token"]
        st.session_state["user_info"] = user_info
        st.session_state["username"] = user_info.get("username", "admin")
        st.session_state["authenticated"] = True
        return True

    except Exception as e:
        logger.error("Auto-login exception: %s", e)
        return False