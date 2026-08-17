"""Chat / RAG Q&A Gradio page."""

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client

# Avatar file paths — Gradio reads these via its own file-serving API
import os.path
_USER_AVATAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "user.svg"))
_BOT_AVATAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "bot.svg"))


def render(auth_state: gr.State, msgs_state: gr.State, conv_id_state: gr.State) -> dict:
    """Render the chat interface. Uses persistent states from app.py."""

    def send_message(message: str, history: list, conv_id: str, auth_json: str) -> tuple:
        """Send a query and get the RAG response."""
        # Auto-create conversation on first message
        if not conv_id:
            client = make_api_client(auth_json)
            try:
                data = client.post("/api/conversations", json={})
                conv_id = data["id"]
            except ApiError as e:
                raise gr.Error(f"Failed to create conversation: {e.detail}")

        client = make_api_client(auth_json)
        try:
            result = client.post(f"/api/conversations/{conv_id}/query", json={
                "query": message,
                "top_k": 5,
            })
            answer = result.get("answer", "No answer generated")
            sources = result.get("sources", [])

            if sources:
                source_text = "\n\n**📎 Sources:**\n" + "\n".join(
                    f"- {s.get('filename', 'Unknown')}" + (f" (p.{s.get('page')})" if s.get('page') else "")
                    for s in sources
                )
                answer += source_text

            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": answer})
            # Return updated state + chatbot
            return history, conv_id, ""
        except ApiError as e:
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": f"❌ Error: {e.detail}"})
            return history, conv_id, ""

    with gr.Column(scale=1):
        gr.Markdown("## 💬 问答", elem_classes="page-header")

        chatbot = gr.Chatbot(
            height=480,
            bubble_full_width=False,
            show_label=False,
            type="messages",
            elem_classes="chat-area",
            avatar_images=(_USER_AVATAR, _BOT_AVATAR),
        )

        with gr.Row(elem_classes="input-area"):
            msg_input = gr.Textbox(
                placeholder="输入您的问题...",
                scale=4,
                show_label=False,
                container=False,
            )
            send_btn = gr.Button("发送", variant="primary", scale=1)

        # send_message writes to both the chatbot display AND the persistent state
        send_btn.click(
            fn=send_message,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, conv_id_state, msg_input],
        ).then(
            fn=lambda h: h,   # Sync the state back to msgs_state after send
            inputs=[chatbot],
            outputs=[msgs_state],
        )

        msg_input.submit(
            fn=send_message,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, conv_id_state, msg_input],
        ).then(
            fn=lambda h: h,
            inputs=[chatbot],
            outputs=[msgs_state],
        )

    return {
        "chatbot": chatbot,
        "msg_input": msg_input,
    }