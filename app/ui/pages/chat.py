"""Chat / RAG Q&A Gradio page."""

import json
import os.path

import gradio as gr
import httpx

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client

# Avatar file paths — Gradio reads these via its own file-serving API
_USER_AVATAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "user.svg"))
_BOT_AVATAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "bot.svg"))

_BASE_URL = "http://localhost:8001"


def render(auth_state: gr.State, msgs_state: gr.State, conv_id_state: gr.State) -> dict:
    """Render the chat interface. Uses persistent states from app.py."""

    def send_message_stream(message: str, history: list, conv_id: str, auth_json: str):
        """Send a query via SSE streaming — tokens appear progressively in the chatbot."""

        # Auto-create conversation on first message
        if not conv_id:
            client = make_api_client(auth_json)
            try:
                data = client.post("/api/conversations", json={})
                conv_id = data["id"]
            except ApiError as e:
                raise gr.Error(f"Failed to create conversation: {e.detail}")

        # Parse auth state for the Authorization header
        from app.ui.auth_helpers import AuthState
        auth = AuthState.from_json(auth_json)

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": ""})
        yield history, history, conv_id, ""  # Clear input, show user message

        assistant_msg = ""
        with httpx.Client(base_url=_BASE_URL, timeout=120.0) as http:
            try:
                with http.stream(
                    "POST",
                    f"/api/conversations/{conv_id}/query/stream",
                    json={"query": message, "top_k": 5},
                    headers={"Authorization": f"Bearer {auth.access_token}"},
                ) as resp:
                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if "token" in data:
                            assistant_msg += data["token"]
                            history[-1] = {"role": "assistant", "content": assistant_msg}
                            yield history, history, conv_id, ""
                        elif data.get("done"):
                            sources = data.get("sources", [])
                            if sources:
                                source_text = "\n\n**📎 Sources:**\n" + "\n".join(
                                    f"- {s.get('filename', 'Unknown')}" + (f" (p.{s.get('page')})" if s.get("page") else "")
                                    for s in sources
                                )
                                history[-1]["content"] += source_text
                            yield history, history, conv_id, ""
            except Exception as e:
                if not assistant_msg:
                    history[-1]["content"] = f"❌ Error: {e}"
                    yield history, history, conv_id, ""

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

        # send_message_stream writes progressively to the chatbot AND persistent state
        send_btn.click(
            fn=send_message_stream,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, msgs_state, conv_id_state, msg_input],
        )

        msg_input.submit(
            fn=send_message_stream,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, msgs_state, conv_id_state, msg_input],
        )

    return {
        "chatbot": chatbot,
        "msg_input": msg_input,
    }