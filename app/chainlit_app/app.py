"""Chainlit application entry point — RAG document Q&A system.

Event handlers:
- @on_chat_start: auto-login, welcome message, navigation actions
- @on_chat_resume: restore conversation context from thread
- @on_message: SSE streaming chat
- Action callbacks: document management, conversation control
"""

import os
import sys
from pathlib import Path

# chainlit run 默认不把项目根目录加入 sys.path，导致 package import 失败。
# 这里手动将项目根目录加入搜索路径，确保 from app.xxx 能找到。
_proj_root = str(Path(__file__).resolve().parent.parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import chainlit as cl

from app.chainlit_app.auth import auto_login
from app.chainlit_app.chat_handler import handle_new_conversation, handle_query
from app.chainlit_app.document_handler import handle_delete, handle_upload, show_documents


@cl.on_chat_start
async def on_chat_start():
    """Initialise session: auto-login, show welcome and navigation actions."""
    await auto_login()

    # Navigation actions — shown as buttons in the chat header
    nav_actions = [
        cl.Action(name="new_conversation", label="📝 新对话", value="new", payload={}),
        cl.Action(name="show_docs", label="📁 文档管理", value="docs", payload={}),
    ]

    await cl.Message(
        content="欢迎使用 **RAG 文档问答系统**！",
        actions=nav_actions,
    ).send()

    cl.user_session.set("conv_id", None)


@cl.on_chat_resume
async def on_chat_resume(thread):
    """Restore conversation context when resuming a previous thread.

    Chainlit auto-restores cl.user_session, so conv_id is carried over.
    """
    nav_actions = [
        cl.Action(name="new_conversation", label="📝 新对话", value="new", payload={}),
        cl.Action(name="show_docs", label="📁 文档管理", value="docs", payload={}),
    ]

    await cl.Message(
        content="已恢复对话，可以继续提问。",
        actions=nav_actions,
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user message — stream RAG answer via SSE."""
    await handle_query(message.content)


# ---------------------------------------------------------------------------
# Action callbacks
# ---------------------------------------------------------------------------


@cl.action_callback("new_conversation")
async def on_new_conversation(action: cl.Action):
    """Start a new conversation (clear current conv_id, reset context)."""
    await handle_new_conversation()
    await action.remove()  # remove the clicked button


@cl.action_callback("show_docs")
async def on_show_docs(action: cl.Action):
    """Switch to document management view."""
    # Build back-to-chat action
    back_actions = [cl.Action(name="back_to_chat", label="💬 返回对话", value="back", payload={})]
    await cl.Message(content="📁 **文档管理**", actions=back_actions).send()
    await show_documents()
    await action.remove()


@cl.action_callback("back_to_chat")
async def on_back_to_chat(action: cl.Action):
    """Return to chat from document management view."""
    nav_actions = [
        cl.Action(name="new_conversation", label="📝 新对话", value="new", payload={}),
        cl.Action(name="show_docs", label="📁 文档管理", value="docs", payload={}),
    ]
    await cl.Message(
        content="已返回对话，请提问。",
        actions=nav_actions,
    ).send()
    await action.remove()


@cl.action_callback("upload_doc")
async def on_upload_doc(action: cl.Action):
    """Upload a document."""
    await handle_upload()
    await action.remove()


@cl.action_callback("delete_doc")
async def on_delete_doc(action: cl.Action):
    """Delete a document by its ID (stored in action.payload["doc_id"])."""
    doc_id = action.payload.get("doc_id")
    if not doc_id:
        await cl.ErrorMessage(content="删除失败：缺少文档 ID").send()
        return
    await handle_delete(doc_id)
    await action.remove()