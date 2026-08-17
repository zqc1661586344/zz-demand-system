"""Gradio application entry point — mounts all pages with custom styling."""

import gradio as gr
import httpx

from app.ui.auth_helpers import AuthState
from app.ui.pages.chat import render as render_chat
from app.ui.pages.documents import render as render_documents
from app.ui.pages.history import render as render_history

CUSTOM_CSS = """
/* reset */
.gradio-container { max-width:100% !important; margin:0 !important; padding:0 !important; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif !important; background:#fff !important; }
*, *::before, *::after { box-sizing:border-box !important; }
footer, .footer, .gr-footer { display:none !important; }

/* brand bar */
#brand-bar > div { display:flex !important; align-items:center !important; justify-content:space-between !important; padding:10px 28px !important; background:linear-gradient(135deg,#1a1a2e,#16213e) !important; min-height:48px !important; }
.brand-title { font-size:18px; font-weight:600; color:#fff; }
.brand-title span { color:#64ffda; }

/* layout */
#app-layout > div { display:flex !important; min-height:calc(100vh - 48px) !important; }
.sidebar { width:200px !important; min-width:200px !important; background:#f8f9fa !important; border-right:1px solid #e9ecef !important; padding:16px 0 !important; }
.main-area { flex:1 !important; background:#fff !important; overflow-y:auto !important; }

/* sidebar buttons */
.sidebar-btn-container { display:flex !important; flex-direction:column !important; gap:2px !important; padding:0 10px !important; margin-bottom:12px !important; }
.sidebar-btn {
    border-radius:8px !important; margin:0 !important; background:transparent !important;
    border:none !important; color:#495057 !important; font-weight:500 !important;
    font-size:14px !important; padding:10px 16px !important; text-align:left !important;
    box-shadow:none !important; transition:all 0.15s ease !important; cursor:pointer !important;
}
.sidebar-btn:hover { background:#e9ecef !important; color:#1a1a2e !important; }
.sidebar-btn.active { background:#1a1a2e !important; color:#fff !important; }
.sidebar-btn.active:hover { background:#16213e !important; color:#fff !important; }

/* hide tab nav */
#main-tabs > div:first-child { display:none !important; }
#main-tabs { border:none !important; }

/* ===== Shared ===== */
.page-header { padding:20px 24px 4px !important; }
.page-header h2 { font-size:20px !important; font-weight:700 !important; color:#1a1a2e !important; margin:0 !important; padding:0 !important; }

/* ===== Chat ===== */
.chat-area { padding:0 24px !important; }
.chat-area .gr-chatbot { border:1px solid #e9ecef !important; border-radius:12px !important; background:#fff !important; box-shadow:0 1px 4px rgba(0,0,0,0.05) !important; min-height:420px !important; }
.input-area { display:flex !important; gap:8px !important; align-items:center !important; padding:8px 24px 24px !important; }
.input-area .gr-text-input { border-radius:8px !important; border:1px solid #dee2e6 !important; font-size:14px !important; }
.input-area button { border-radius:8px !important; font-weight:600 !important; }

/* ===== History ===== */
.history-content { padding:0 24px !important; }
.history-table { border:1px solid #e9ecef !important; border-radius:12px !important; box-shadow:0 1px 4px rgba(0,0,0,0.05) !important; overflow:hidden !important; }
.history-table table { border:none !important; }
.history-table th { background:#f8f9fa !important; font-weight:600 !important; font-size:13px !important; color:#495057 !important; border-bottom:2px solid #e9ecef !important; }
.history-table td { font-size:13px !important; color:#212529 !important; }
.history-table tr:nth-child(even) td { background:#f8f9fa !important; }

/* ===== Documents ===== */
.doc-content { padding:0 24px !important; }
.doc-table { border:1px solid #e9ecef !important; border-radius:12px !important; box-shadow:0 1px 4px rgba(0,0,0,0.05) !important; overflow:hidden !important; }
.doc-table table { border:none !important; }
.doc-table th { background:#f8f9fa !important; font-weight:600 !important; font-size:13px !important; color:#495057 !important; border-bottom:2px solid #e9ecef !important; }
.doc-table td { font-size:13px !important; color:#212529 !important; }
.doc-table tr:nth-child(even) td { background:#f8f9fa !important; }
.doc-upload { background:#f8f9fa !important; border-radius:12px !important; padding:20px !important; border:2px dashed #dee2e6 !important; text-align:center !important; }
.doc-upload h3 { font-size:15px !important; font-weight:600 !important; color:#1a1a2e !important; margin:0 0 12px 0 !important; }
.doc-upload .gr-upload-button { width:100% !important; }
.doc-msg { padding:0 24px 24px !important; }
"""


def _auto_login() -> str:
    try:
        resp = httpx.post(
            "http://localhost:8000/api/auth/login",
            json={"username": "admin", "password": "admin123"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            me = httpx.get(
                "http://localhost:8000/api/auth/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
                timeout=10,
            )
            user_info = (
                me.json() if me.status_code == 200 else {"username": "admin", "roles": ["admin"]}
            )
            state = AuthState(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                user_info=user_info,
            )
            return state.to_json()
    except Exception:
        pass
    return ""


def create_app() -> gr.Blocks:
    with gr.Blocks(css=CUSTOM_CSS, title="EnterpISE RAG System", theme=gr.themes.Soft()) as app:
        auth_state = gr.State(_auto_login)

        # Persistent states for chat (live outside tabs to survive tab switches)
        chat_msgs_state = gr.State([])
        chat_conv_id_state = gr.State("")
        # Brand bar
        with gr.Row(elem_id="brand-bar"):
            gr.Markdown('<div class="brand-title">📖 <span>RAG</span> 文档问答系统</div>')

        # Sidebar + content
        with gr.Row(elem_id="app-layout"):
            # Sidebar — 3 nav buttons
            with gr.Column(elem_classes="sidebar", scale=0, min_width=200):
                gr.HTML('<div class="sidebar-btn-container">')
                new_conv_btn = gr.Button("📝  新对话", elem_id="nav-new", elem_classes="sidebar-btn active")
                history_btn = gr.Button("📋  对话历史", elem_id="nav-history", elem_classes="sidebar-btn")
                doc_btn = gr.Button("📁  文档", elem_id="nav-docs", elem_classes="sidebar-btn")
                gr.HTML('</div>')

            # Content — 3 hidden tabs
            with gr.Column(elem_classes="main-area", scale=1):
                with gr.Tabs(elem_id="main-tabs", selected=0) as main_tabs:
                    with gr.TabItem("问答", id=0):
                        chat_refs = render_chat(auth_state, chat_msgs_state, chat_conv_id_state)
                    with gr.TabItem("对话历史", id=1):
                        history_refs = render_history(auth_state)
                    with gr.TabItem("文档", id=2):
                        render_documents(auth_state)

        # ---- Event wiring ----

        # Helper: returns (new_btn_classes, history_btn_classes, doc_btn_classes)
        def _active_new():
            return (
                gr.update(elem_classes="sidebar-btn active"),
                gr.update(elem_classes="sidebar-btn"),
                gr.update(elem_classes="sidebar-btn"),
            )

        def _active_history():
            return (
                gr.update(elem_classes="sidebar-btn"),
                gr.update(elem_classes="sidebar-btn active"),
                gr.update(elem_classes="sidebar-btn"),
            )

        def _active_docs():
            return (
                gr.update(elem_classes="sidebar-btn"),
                gr.update(elem_classes="sidebar-btn"),
                gr.update(elem_classes="sidebar-btn active"),
            )

        # New conversation → switch to chat tab + clear conv_id (so next msg creates new conversation)
        # but preserve displayed messages so switching back from other tabs doesn't lose them
        def _switch_to_chat(saved_msgs: list):
            return gr.Tabs(selected=0), saved_msgs, ""

        new_conv_btn.click(
            fn=_switch_to_chat,
            inputs=[chat_msgs_state],
            outputs=[main_tabs, chat_refs["chatbot"], chat_conv_id_state],
        ).then(
            fn=_active_new,
            outputs=[new_conv_btn, history_btn, doc_btn],
        )

        # History button → load history table + switch to history tab + active history
        def load_history(auth_json: str):
            data = history_refs["load_all"](auth_json)
            return data, gr.Tabs(selected=1)

        history_btn.click(
            fn=load_history,
            inputs=[auth_state],
            outputs=[history_refs["conv_table"], main_tabs],
        ).then(
            fn=_active_history,
            outputs=[new_conv_btn, history_btn, doc_btn],
        )

        # Document button → switch to doc tab + active docs
        doc_btn.click(
            fn=lambda: gr.Tabs(selected=2),
            outputs=[main_tabs],
        ).then(
            fn=_active_docs,
            outputs=[new_conv_btn, history_btn, doc_btn],
        )

        # History table row click → load that conversation in chat
        def load_conversation(evt: gr.SelectData, auth_json: str):
            """When a row is clicked in history table, load messages into chat."""
            from app.ui.api_client import ApiError, make_api_client

            row_data = evt.value
            conv_id = row_data[0]  # First column is the conversation ID
            if not conv_id:
                return [], "", gr.Tabs(selected=0)

            client = make_api_client(auth_json)
            try:
                msgs = client.get(f"/api/conversations/{conv_id}/messages")
                history = [
                    {"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                    for m in msgs
                ]
                return history, conv_id, gr.Tabs(selected=0)
            except ApiError:
                return [], "", gr.Tabs(selected=0)

        history_refs["conv_table"].select(
            fn=load_conversation,
            inputs=[auth_state],
            outputs=[chat_msgs_state, chat_conv_id_state, main_tabs],
        ).then(
            fn=_active_new,
            outputs=[new_conv_btn, history_btn, doc_btn],
        )

        return app


app = create_app()
