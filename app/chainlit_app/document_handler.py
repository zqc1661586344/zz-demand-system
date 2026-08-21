"""Document management handler — list, upload, delete documents."""

import logging
import os

import chainlit as cl

from app.chainlit_app.api_client import ApiError
from app.chainlit_app.auth import get_api_client

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = [".pdf", ".txt", ".md", ".docx"]
MAX_SIZE_MB = 50


def _format_size(size_bytes: int) -> str:
    """Format file size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


async def show_documents():
    """Fetch and display document list.

    Sends a header message with action buttons, then one message per document
    with an individual delete action.
    """
    client = await get_api_client()
    try:
        docs = await client.get("/api/documents")
    except ApiError as e:
        await cl.ErrorMessage(content=f"获取文档列表失败：{e.detail}").send()
        return

    # Header with global actions
    header_actions = [
        cl.Action(name="upload_doc", label="📎 上传文档", value="upload", payload={}),
    ]
    await cl.Message(
        content="📁 **文档管理** — 共 {} 个文档".format(len(docs)),
        actions=header_actions,
    ).send()

    if not docs:
        await cl.Message(content="暂无文档，点击上方按钮上传。").send()
        return

    # One message per document with a delete action
    for doc in docs:
        filename = doc.get("original_filename", "unknown")
        status = doc.get("status", "unknown")
        size = _format_size(doc.get("file_size", 0))
        chunks = doc.get("chunk_count") or "—"
        created = (doc.get("created_at") or "")[:19]
        doc_id = doc["id"]

        content = (
            f"📄 **{filename}**\n"
            f"- 状态：{status}\n"
            f"- 大小：{size}\n"
            f"- 分块数：{chunks}\n"
            f"- 上传时间：{created}"
        )

        doc_actions = [
            cl.Action(name="delete_doc", label="🗑 删除", value=doc_id, payload={"doc_id": doc_id}),
        ]
        await cl.Message(content=content, actions=doc_actions).send()


async def handle_upload():
    """Handle document upload via file picker.

    Uses cl.AskFileMessage to let the user pick a file, then uploads it via
    multipart POST to the backend.
    """
    files = await cl.AskFileMessage(
        content="请选择要上传的文档文件（PDF / TXT / MD / DOCX，最大 50MB）",
        accept=SUPPORTED_EXTENSIONS,
        max_size_mb=MAX_SIZE_MB,
    ).send()

    if not files:
        return

    file = files[0]  # single-file upload
    file_bytes = file.content

    # Determine MIME type from extension
    ext = os.path.splitext(file.name)[1].lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mime_type = mime_map.get(ext, "application/octet-stream")

    client = await get_api_client()
    try:
        await client.upload(
            "/api/documents/upload",
            files={"file": (file.name, file_bytes, mime_type)},
        )
        await cl.Message(content=f"✅ **{file.name}** 上传成功，正在处理中…").send()
    except ApiError as e:
        await cl.ErrorMessage(content=f"上传失败：{e.detail}").send()

    # Refresh document list
    await show_documents()


async def handle_delete(doc_id: str):
    """Delete a document by ID, then refresh the document list."""
    client = await get_api_client()
    try:
        await client.delete(f"/api/documents/{doc_id}")
        await cl.Message(content="✅ 文档已删除").send()
    except ApiError as e:
        await cl.ErrorMessage(content=f"删除失败：{e.detail}").send()

    await show_documents()