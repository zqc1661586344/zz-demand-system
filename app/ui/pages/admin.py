"""Admin user management Gradio page."""

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client


def render(auth_state: gr.State) -> gr.Blocks:
    """Render the admin user management page."""

    def load_users(auth_json: str) -> list[list]:
        client = make_api_client(auth_json)
        try:
            users = client.get("/api/users")
            return [
                [u["username"], u["email"], u.get("full_name", "") or "", ", ".join(u.get("roles", [])), "✅" if u["is_active"] else "❌"]
                for u in users
            ]
        except ApiError:
            return []

    def toggle_user(user_id: str, auth_json: str) -> str:
        client = make_api_client(auth_json)
        try:
            users = client.get("/api/users")
            target = next((u for u in users if u["username"] == user_id or u["id"] == user_id), None)
            if target:
                client.put(f"/api/users/{target['id']}", json={"is_active": not target["is_active"]})
                return f"✅ User '{target['username']}' status toggled"
            return "❌ User not found"
        except ApiError as e:
            return f"❌ {e.detail}"

    def update_roles(user_id: str, roles_str: str, auth_json: str) -> str:
        client = make_api_client(auth_json)
        try:
            users = client.get("/api/users")
            target = next((u for u in users if u["username"] == user_id or u["id"] == user_id), None)
            if target:
                role_names = [r.strip() for r in roles_str.split(",") if r.strip()]
                client.put(f"/api/users/{target['id']}/roles", json={"role_names": role_names})
                return f"✅ Roles updated for '{target['username']}'"
            return "❌ User not found"
        except ApiError as e:
            return f"❌ {e.detail}"

    with gr.Column(scale=1):
        gr.Markdown("## ⚙ 用户管理")

        user_table = gr.Dataframe(
            headers=["用户名", "邮箱", "全名", "角色", "活跃"],
            label="用户列表",
            interactive=False,
        )

        with gr.Tabs():
            with gr.TabItem("切换用户状态"):
                toggle_user_id = gr.Textbox(label="用户名或ID", placeholder="Username to toggle")
                toggle_btn = gr.Button("切换状态", variant="secondary")
                toggle_msg = gr.Textbox(label="", interactive=False)

            with gr.TabItem("更新角色"):
                role_user_id = gr.Textbox(label="用户名或ID", placeholder="Username")
                role_names = gr.Textbox(label="角色 (逗号分隔)", placeholder="admin, editor, viewer")
                role_btn = gr.Button("更新角色", variant="secondary")
                role_msg = gr.Textbox(label="", interactive=False)

        # Events
        gr.on(
            triggers=[auth_state.change],
            fn=load_users,
            inputs=[auth_state],
            outputs=[user_table],
        )

        toggle_btn.click(
            fn=toggle_user,
            inputs=[toggle_user_id, auth_state],
            outputs=[toggle_msg],
        ).then(
            fn=load_users,
            inputs=[auth_state],
            outputs=[user_table],
        )

        role_btn.click(
            fn=update_roles,
            inputs=[role_user_id, role_names, auth_state],
            outputs=[role_msg],
        ).then(
            fn=load_users,
            inputs=[auth_state],
            outputs=[user_table],
        )

    return user_table