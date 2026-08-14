"""Chat / RAG Q&A Gradio page."""

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client


def render(auth_state: gr.State) -> gr.Blocks:
    """Render the chat interface."""

    def load_conversations(auth_json: str) -> list:
        client = make_api_client(auth_json)
        try:
            return [(c["id"], c["title"] or "Untitled") for c in client.get("/api/conversations")]
        except ApiError:
            return []

    def create_conversation(auth_json: str) -> str:
        client = make_api_client(auth_json)
        try:
            data = client.post("/api/conversations", json={})
            return data["id"]
        except ApiError as e:
            raise gr.Error(f"Failed to create conversation: {e.detail}")

    def send_message(message: str, history: list, conv_id: str, auth_json: str) -> tuple:
        """Send a query and get the RAG response."""
        if not conv_id:
            conv_id = create_conversation(auth_json)

        client = make_api_client(auth_json)
        try:
            result = client.post(f"/api/conversations/{conv_id}/query", json={
                "query": message,
                "top_k": 5,
            })
            answer = result.get("answer", "No answer generated")
            sources = result.get("sources", [])

            # Format sources
            if sources:
                source_text = "\n\n**📎 Sources:**\n" + "\n".join(
                    f"- {s.get('filename', 'Unknown')}" + (f" (p.{s.get('page')})" if s.get('page') else "")
                    for s in sources
                )
                answer += source_text

            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": answer})
            return history, conv_id, ""
        except ApiError as e:
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": f"❌ Error: {e.detail}"})
            return history, conv_id, ""

    with gr.Column(scale=1):
        with gr.Row():
            new_conv_btn = gr.Button("📝 新对话", scale=1)
            conv_dropdown = gr.Dropdown(
                label="对话历史",
                choices=[],
                interactive=True,
                scale=2,
            )

        chatbot = gr.Chatbot(
            label="问答",
            height=500,
            bubble_full_width=False,
            show_label=False,
            type="messages",
        )

        with gr.Row():
            msg_input = gr.Textbox(
                label="输入问题",
                placeholder="输入您的问题...",
                scale=4,
                show_label=False,
                container=False,
            )
            send_btn = gr.Button("发送", variant="primary", scale=1)

        conv_id_state = gr.State("")

        # Event handlers
        def on_page_load(auth_json: str):
            convs = load_conversations(auth_json)
            return gr.update(choices=convs)

        gr.on(
            triggers=[auth_state.change],
            fn=on_page_load,
            inputs=[auth_state],
            outputs=[conv_dropdown],
        )

        send_btn.click(
            fn=send_message,
            inputs=[msg_input, chatbot, conv_id_state, auth_state],
            outputs=[chatbot, conv_id_state, msg_input],
        )
        msg_input.submit(
            fn=send_message,
            inputs=[msg_input, chatbot, conv_id_state, auth_state],
            outputs=[chatbot, conv_id_state, msg_input],
        )

        new_conv_btn.click(
            fn=lambda: ("", [], ""),
            outputs=[conv_id_state, chatbot, msg_input],
        )

    return chatbot