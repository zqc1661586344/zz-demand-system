"""Streamlit application entry point — RAG document Q&A system.

Two-page layout:
- Chat (default): SSE streaming RAG conversation
- Documents: upload, list, delete documents

Uses st.navigation (multipage) or sidebar selectbox for page switching.
Since streamlit run doesn't use the folder-as-pages convention here,
we manage pages manually via a sidebar selectbox.
"""

import os
import sys
from pathlib import Path

# streamlit run 默认不把项目根目录加入 sys.path，导致 package import 失败。
# 这里手动将项目根目录加入搜索路径，确保 from app.xxx 能找到。
_proj_root = str(Path(__file__).resolve().parent.parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import logging

import streamlit as st

from app.streamlit_app.auth import auto_login
from app.streamlit_app.views.chat import new_conversation, page as chat_page
from app.streamlit_app.views.documents import page as documents_page

logger = logging.getLogger(__name__)


def main():
    st.set_page_config(
        page_title="RAG 文档问答系统",
        page_icon="📚",
        layout="wide",
    )

    # Auto-login on every load
    if "authenticated" not in st.session_state:
        if not auto_login():
            st.error("自动登录失败，请确认后端服务已启动（端口 8001）")
            st.stop()

    # Page selection persisted in URL query param (?page=chat|docs) so that
    # browser refresh (F5) keeps the user on the same page instead of resetting
    # to chat when a fresh Streamlit session is created.
    page = st.query_params.get("page", "chat")
    page_options = ["💬 对话", "📁 文档管理"]
    page_keys = ["chat", "docs"]

    # Find the radio index matching the current page
    try:
        radio_idx = page_keys.index(page)
    except ValueError:
        radio_idx = 0

    with st.sidebar:
        st.markdown(f"👤 **{st.session_state.get('username', '用户')}**")
        st.divider()

        choice = st.radio(
            "导航",
            options=page_options,
            index=radio_idx,
            label_visibility="collapsed",
            key="nav",
        )

        # If the user clicked a different page option, update URL param and rerun
        selected_key = page_keys[page_options.index(choice)]
        if selected_key != page:
            st.query_params["page"] = selected_key
            st.rerun()

        # Sidebar extras shown only on the chat page
        if page == "chat":
            if st.button("📝 新对话", use_container_width=True):
                new_conversation()
                st.rerun()

        st.divider()
        st.caption(f"RAG 文档问答系统 v0.1.0")

    # Page routing
    if page == "docs":
        documents_page()
    else:
        chat_page()


if __name__ == "__main__":
    main()