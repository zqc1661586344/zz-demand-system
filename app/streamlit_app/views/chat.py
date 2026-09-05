"""Chat page — SSE streaming conversation."""

import json
from datetime import datetime

import httpx
import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import BASE_URL, ApiError, request

logger = get_logger(__name__)

TOP_K = 5

# 【根治】："找不到答案"提示语由前端按 free_chat 标记渲染，不进入模型输出路径
FREE_CHAT_PREFIX = "**当前已有文档中找不到答案，以下由大模型自身知识回答：**\n\n"


def _ensure_conversation() -> str:
    """Get or create the current conversation ID from session state."""
    conv_id = st.session_state.get("conv_id")
    if conv_id:
        # Persist to URL so page refresh (F5) can recover it
        st.query_params["conv_id"] = conv_id
        return conv_id

    try:
        data = request("POST", "/api/conversations", json={})
        conv_id = data["id"]
        st.session_state["conv_id"] = conv_id
        st.query_params["conv_id"] = conv_id  # persist for refresh
        return conv_id
    except ApiError as e:
        st.error(f"创建对话失败：{e.detail}")
        raise


def _load_messages(conv_id: str):
    """Load conversation history and populate chat_message list in session state."""
    try:
        data = request("GET", f"/api/conversations/{conv_id}/messages")
        msgs = data.get("items", []) if isinstance(data, dict) else (data or [])
        conv_messages = []
        for m in msgs:
            entry = {"role": m["role"], "content": m["content"]}
            # Backend stores sources as a JSON string; parse it for display
            raw_sources = m.get("sources")
            if raw_sources:
                try:
                    sources_list = json.loads(raw_sources)
                    if sources_list:
                        entry["sources"] = sources_list
                except (json.JSONDecodeError, TypeError):
                    pass
            # 自由聊天标记持久化在数据库，前端据此渲染提示语
            if m.get("free_chat"):
                entry["free_chat"] = True
            conv_messages.append(entry)
        st.session_state["conv_messages"] = conv_messages
    except ApiError as e:
        logger.warning("Failed to load messages: %s", e)
        st.session_state["conv_messages"] = []


def _display_messages():
    """Render all messages from session state into Streamlit chat UI."""
    for m in st.session_state["conv_messages"]:
        with st.chat_message(m["role"]):
            # 自由聊天消息：前端补上提示语（纯模型回答存库，不含提示语）
            content = m["content"]
            if m.get("free_chat"):
                content = FREE_CHAT_PREFIX + content
            st.markdown(content)
            # Show sources for assistant messages
            sources = m.get("sources")
            if sources:
                source_lines = "\n".join(
                    f"- 📄 {s.get('filename', 'Unknown')}"
                    + (f" (p.{s['page']})" if s.get("page") else "")
                    for s in sources
                )
                st.markdown("---\n**📎 来源文档**\n" + source_lines)


def page():
    """Chat page UI — rendered inside the main app's tab."""
    conv_id = st.session_state.get("conv_id")

    # Recover conv_id from URL query param on page refresh (F5)
    if conv_id is None:
        url_conv_id = st.query_params.get("conv_id")
        if url_conv_id:
            conv_id = url_conv_id
            st.session_state["conv_id"] = conv_id

    # Load messages if we have a conversation but none in state yet
    if conv_id and "conv_messages" not in st.session_state:
        _load_messages(conv_id)

    # Display existing messages
    if "conv_messages" in st.session_state:
        _display_messages()
    else:
        st.session_state["conv_messages"] = []

    # Chat input
    prompt = st.chat_input("请输入你的问题…")

    if not prompt:
        return

    # Show user message immediately
    st.session_state["conv_messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Ensure conversation exists
    try:
        conv_id = _ensure_conversation()
    except ApiError:
        return

    # Stream assistant response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_answer = ""
        sources = []
        free_chat = False  # 由后端 free_chat 事件置位，前端据此渲染提示语

        access_token = st.session_state.get("access_token")

        try:
            with httpx.Client(base_url=BASE_URL, timeout=120.0) as http:
                with http.stream(
                    "POST",
                    f"/api/conversations/{conv_id}/query/stream",
                    json={"query": prompt, "top_k": TOP_K},
                    headers={"Authorization": f"Bearer {access_token}"},
                ) as resp:
                    if resp.status_code >= 400:
                        # httpx 流式模式下必须先 read() 才能访问响应体
                        resp.read()
                        try:
                            detail = resp.json().get("detail", resp.text)
                        except Exception:
                            detail = resp.text
                        st.error(f"请求失败（{resp.status_code}）：{detail}")
                        return

                    for raw in resp.iter_lines():
                        if not raw or not raw.startswith("data: "):
                            continue
                        data_str = raw[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if data.get("error"):
                            st.error(f"生成失败：{data['error']}")
                            return

                        if data.get("free_chat"):
                            free_chat = True

                        if "token" in data:
                            full_answer += data["token"]
                            display = FREE_CHAT_PREFIX + full_answer if free_chat else full_answer
                            placeholder.markdown(display + "▌")

                        if "sources" in data:
                            sources = data.get("sources", [])

        except httpx.TimeoutException:
            st.error("请求超时，请重试。")
            return
        except Exception as e:
            st.error(f"网络错误：{e}")
            return

        # Final render — 自由聊天时由前端渲染提示语，纯模型回答仍存库
        display = FREE_CHAT_PREFIX + full_answer if free_chat else full_answer
        placeholder.markdown(display)

        # Show sources
        if sources:
            source_lines = "\n".join(
                f"- 📄 {s.get('filename', 'Unknown')}"
                + (f" (p.{s['page']})" if s.get("page") else "")
                for s in sources
            )
            st.markdown("---\n**📎 来源文档**\n" + source_lines)

    # Save messages to state for next render (including sources)
    entry = {"role": "assistant", "content": full_answer, "free_chat": free_chat}
    if sources:
        entry["sources"] = sources
    st.session_state["conv_messages"].append(entry)


def new_conversation():
    """Start a new conversation."""
    st.session_state["conv_id"] = None
    st.session_state["conv_messages"] = []
    st.query_params.pop("conv_id", None)  # clear URL param too
