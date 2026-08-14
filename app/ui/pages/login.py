"""Login/Register Gradio page."""

import gradio as gr

from app.ui.api_client import ApiClient, ApiError
from app.ui.auth_helpers import AuthState


def render() -> tuple:
    """Render login and register forms. Returns (login_block, auth_state_output)."""

    def login_handler(username: str, password: str) -> tuple:
        """Authenticate user and return serialised AuthState + redirect signal."""
        client = ApiClient()
        try:
            data = client.post("/api/auth/login", json={"username": username, "password": password})
            user_info = client.get("/api/auth/me")
            state = AuthState(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                user_info=user_info,
            )
            return state.to_json(), gr.update(visible=False), gr.update(visible=True), ""
        except ApiError as e:
            return "", gr.update(visible=True), gr.update(visible=False), f"Login failed: {e.detail}"

    def register_handler(username: str, password: str, email: str, full_name: str) -> tuple:
        """Register a new user and return serialised AuthState."""
        client = ApiClient()
        try:
            data = client.post("/api/auth/register", json={
                "username": username,
                "password": password,
                "email": email,
                "full_name": full_name or None,
            })
            client.access_token = data["access_token"]
            user_info = client.get("/api/auth/me")
            state = AuthState(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                user_info=user_info,
            )
            return state.to_json(), gr.update(visible=False), gr.update(visible=True), ""
        except ApiError as e:
            return "", gr.update(visible=True), gr.update(visible=False), f"Registration failed: {e.detail}"

    with gr.Column(scale=1, min_width=400) as login_block:
        gr.Markdown("## 🔐 登录")
        username = gr.Textbox(label="用户名", placeholder="Enter username")
        password = gr.Textbox(label="密码", type="password", placeholder="Enter password")
        login_btn = gr.Button("登录", variant="primary")
        login_msg = gr.Markdown("", visible=False)

        gr.Markdown("---\n### 📝 注册新用户")
        reg_username = gr.Textbox(label="用户名", placeholder="Choose a username")
        reg_email = gr.Textbox(label="邮箱", placeholder="email@example.com")
        reg_password = gr.Textbox(label="密码", type="password", placeholder="Choose a password")
        reg_full_name = gr.Textbox(label="全名 (可选)", placeholder="Your full name")
        reg_btn = gr.Button("注册", variant="secondary")
        reg_msg = gr.Markdown("", visible=False)

        auth_state = gr.State("")
        main_content = gr.Column(visible=False)
        login_container = gr.Column(visible=True)

        login_btn.click(
            fn=login_handler,
            inputs=[username, password],
            outputs=[auth_state, login_container, main_content, login_msg],
        )
        reg_btn.click(
            fn=register_handler,
            inputs=[reg_username, reg_password, reg_email, reg_full_name],
            outputs=[auth_state, login_container, main_content, reg_msg],
        )

    return login_block, auth_state, login_container, main_content