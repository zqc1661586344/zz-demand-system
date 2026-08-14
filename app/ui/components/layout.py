"""Gradio layout components — sidebar navigation and shared UI."""

import gradio as gr


def create_sidebar(current_page: str = "chat", is_admin: bool = False) -> gr.HTML:
    """Render the sidebar navigation as HTML."""
    pages = [
        ("chat", "💬 问答", "/ui"),
        ("documents", "📁 文档", "/ui/?page=documents"),
        ("workflows", "🔄 流程", "/ui/?page=workflows"),
    ]

    if is_admin:
        pages.append(("admin", "⚙ 管理", "/ui/?page=admin"))

    items = []
    for page_id, label, href in pages:
        active = "background: var(--button-primary-background-fill); color: white;" if page_id == current_page else ""
        items.append(
            f'<a href="{href}" style="display: block; padding: 10px 16px; '
            f'text-decoration: none; color: inherit; border-radius: 8px; {active} '
            f'margin-bottom: 4px;">{label}</a>'
        )

    html = f"""
    <div style="
        padding: 16px;
        min-width: 180px;
        height: 100%;
        border-right: 1px solid var(--border-color-primary, #e0e0e0);
    ">
        <div style="font-size: 1.1em; font-weight: 600; margin-bottom: 16px; padding: 0 16px;">
            📖 RAG 系统
        </div>
        <nav>
            {''.join(items)}
        </nav>
    </div>
    """
    return gr.HTML(html)


def logout_button() -> gr.Button:
    return gr.Button("🚪 退出登录", variant="secondary", size="sm")