"""Conversation history page — list past conversations, click to load."""

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client


def render(auth_state: gr.State) -> dict:
    """Render the conversation history page."""

    def load_all(auth_json: str) -> list:
        """Load all conversations for the current user."""
        client = make_api_client(auth_json)
        try:
            convs = client.get("/api/conversations")
            return [
                [c["id"], c["title"] or "Untitled", c.get("message_count", 0), c["updated_at"][:19]]
                for c in convs
            ]
        except ApiError:
            return []

    with gr.Column(scale=1):
        gr.Markdown("## 📋 对话历史", elem_classes="page-header")
        conv_table = gr.Dataframe(
            headers=["ID", "标题", "消息数", "更新时间"],
            label="历史对话记录",
            interactive=False,
            wrap=True,
            elem_classes="history-table",
        )

    return {
        "conv_table": conv_table,
        "load_all": load_all,
    }