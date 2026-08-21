"""Document management page — list, upload, delete documents."""

import logging
import os
import tempfile

import streamlit as st

from app.streamlit_app.api_client import ApiError, delete, get, upload

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = [".pdf", ".txt", ".md", ".docx"]
MAX_SIZE_MB = 50


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _refresh_doc_list():
    """Fetch document list from backend and store in session state."""
    try:
        docs = get("/api/documents")
        st.session_state["doc_list"] = docs
    except ApiError as e:
        st.error(f"获取文档列表失败：{e.detail}")
        st.session_state["doc_list"] = []


def page():
    """Document management page UI."""
    st.markdown("## 📁 文档管理")

    # Upload section — key includes a counter so we can "reset" the widget by
    # incrementing the counter, forcing a fresh widget (no stale file value).
    upload_key = st.session_state.get("_upload_key", 0)

    st.markdown("### 上传文档")
    uploaded_file = st.file_uploader(
        "选择文件",
        type=SUPPORTED_EXTENSIONS,
        help=f"支持 {', '.join(SUPPORTED_EXTENSIONS)}，最大 {MAX_SIZE_MB}MB",
        key=f"file_uploader_{upload_key}",
    )

    if uploaded_file is not None and st.session_state.get("_upload_handled") is None:
        if uploaded_file.size > MAX_SIZE_MB * 1024 * 1024:
            st.error(f"文件过大（{_format_size(uploaded_file.size)}），最大 {MAX_SIZE_MB}MB")
        else:
            with st.spinner(f"上传 {uploaded_file.name}…"):
                ext = os.path.splitext(uploaded_file.name)[1].lower()
                mime_map = {
                    ".pdf": "application/pdf",
                    ".txt": "text/plain",
                    ".md": "text/markdown",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
                mime_type = mime_map.get(ext, "application/octet-stream")

                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

                try:
                    upload("/api/documents/upload", tmp_path, uploaded_file.name, mime_type)
                    st.success(f"✅ **{uploaded_file.name}** 上传成功，正在处理中…")
                    # Mark handled + increment upload key so the widget resets itself
                    st.session_state["_upload_handled"] = True
                    st.session_state["_upload_key"] = upload_key + 1
                    _refresh_doc_list()
                    st.rerun()
                except ApiError as e:
                    st.error(f"上传失败：{e.detail}")
                finally:
                    os.unlink(tmp_path)

    # Document list — only fetch from API if we haven't loaded yet or after mutation
    if st.session_state.get("doc_list") is None:
        _refresh_doc_list()

    docs = st.session_state.get("doc_list", [])

    if not docs:
        st.info("暂无文档，请上传。")
        return

    # Table view
    table_data = []
    for doc in docs:
        table_data.append(
            {
                "文件名": doc.get("original_filename", "unknown"),
                "状态": doc.get("status", "unknown"),
                "大小": _format_size(doc.get("file_size", 0)),
                "分块数": doc.get("chunk_count") or "—",
                "上传时间": (doc.get("created_at") or "")[:19],
                "id": doc["id"],
            }
        )

    # Display as a simple table with delete buttons
    for i, row in enumerate(table_data):
        col1, col2, col3, col4, col5, col6 = st.columns([3, 1, 1, 1, 2, 1])
        with col1:
            st.markdown(f"📄 **{row['文件名']}**")
        with col2:
            st.caption(f"状态: {row['状态']}")
        with col3:
            st.caption(f"大小: {row['大小']}")
        with col4:
            st.caption(f"分块: {row['分块数']}")
        with col5:
            st.caption(row["上传时间"])
        with col6:
            if st.button("🗑", key=f"del_{row['id']}", help="删除"):
                try:
                    delete(f"/api/documents/{row['id']}")
                    st.success("✅ 文档已删除")
                    # Refresh list immediately before rerun so the new state is ready
                    _refresh_doc_list()
                    st.rerun()
                except ApiError as e:
                    st.error(f"删除失败：{e.detail}")

    st.caption(f"共 {len(docs)} 个文档")