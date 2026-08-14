"""Gradio application entry point — mounts all pages with custom styling."""
import gradio as gr
import httpx

from app.ui.auth_helpers import AuthState
from app.ui.pages.admin import render as render_admin
from app.ui.pages.chat import render as render_chat
from app.ui.pages.documents import render as render_documents
from app.ui.pages.workflows import render as render_workflows

CUSTOM_CSS = """
/* reset */
.gradio-container { max-width:100% !important; margin:0 !important; padding:0 !important; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif !important; background:#fff !important; }
footer, .footer, .gr-footer { display:none !important; }

/* brand bar */
#brand-bar > div { display:flex !important; align-items:center !important; justify-content:space-between !important; padding:10px 28px !important; background:linear-gradient(135deg,#1a1a2e,#16213e) !important; min-height:48px !important; }
.brand-title { font-size:18px; font-weight:600; color:#fff; }
.brand-title span { color:#64ffda; }
.brand-user { font-size:13px; color:rgba(255,255,255,0.65); }

/* layout */
#app-layout > div { display:flex !important; min-height:calc(100vh - 48px) !important; }
.sidebar { width:200px !important; min-width:200px !important; background:#f8f9fa !important; border-right:1px solid #e9ecef !important; padding:16px 0 !important; }
.main-area { flex:1 !important; background:#fff !important; overflow-y:auto !important; }

/* sidebar btns */
.sidebar-btn {
    border-radius:8px !important; margin:2px 10px !important; background:transparent !important;
    border:none !important; color:#495057 !important; font-weight:500 !important;
    font-size:14px !important; padding:10px 16px !important; text-align:left !important;
    box-shadow:none !important; transition:all 0.12s ease !important;
}
.sidebar-btn:hover { background:#e9ecef !important; color:#1a1a2e !important; }
.sidebar-btn.active { background:#1a1a2e !important; color:#fff !important; }

/* hide tab nav */
#main-tabs .tab-nav { display:none !important; }
#main-tabs { border:none !important; }
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
            user_info = me.json() if me.status_code == 200 else {"username": "admin", "roles": ["admin"]}
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
    with gr.Blocks(css=CUSTOM_CSS, title="Enterprise RAG System", theme=gr.themes.Soft()) as app:
        auth_state = gr.State(_auto_login)

        # Brand bar
        with gr.Row(elem_id="brand-bar"):
            gr.Markdown('<div class="brand-title">📖 <span>RAG</span> 文档问答系统</div>')
            gr.Markdown('<div class="brand-user">admin · admin</div>')

        # Sidebar + content
        with gr.Row(elem_id="app-layout"):
            # Sidebar
            with gr.Column(elem_classes="sidebar", scale=0, min_width=200):
                chat_btn = gr.Button("💬  问答", elem_classes="sidebar-btn active")
                doc_btn = gr.Button("📁  文档", elem_classes="sidebar-btn")
                wkf_btn = gr.Button("🔄  流程", elem_classes="sidebar-btn")
                admin_btn = gr.Button("⚙  管理", elem_classes="sidebar-btn")

            # Content (hidden tab nav, switched by sidebar)
            with gr.Column(elem_classes="main-area", scale=1):
                with gr.Tabs(elem_id="main-tabs", selected=0) as main_tabs:
                    with gr.TabItem("问答", id=0):
                        render_chat(auth_state)
                    with gr.TabItem("文档", id=1):
                        render_documents(auth_state)
                    with gr.TabItem("流程", id=2):
                        render_workflows(auth_state)
                    with gr.TabItem("管理", id=3):
                        render_admin(auth_state)

        # Sidebar → tab switching
        for btn, idx in [(chat_btn, 0), (doc_btn, 1), (wkf_btn, 2), (admin_btn, 3)]:
            btn.click(fn=lambda i=idx: gr.Tabs(selected=i), outputs=[main_tabs])

    return app


app = create_app()