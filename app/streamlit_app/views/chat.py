"""Chat page — SSE streaming conversation."""

import json
import logging
from datetime import datetime

import httpx
import streamlit as st

from app.streamlit_app.api_client import BASE_URL, ApiError, request

logger = logging.getLogger(__name__)

TOP_K = 5


def _ensure_conversation() -> str:
    """Get or create the current conversation ID from session state."""
    conv_id = st.session_state.get("conv_id")
    if conv_id:
        return conv_id

    try:
        data = request("POST", "/api/conversations", json={})
        conv_id = data["id"]
        st.session_state["conv_id"] = conv_id
        return conv_id
    except ApiError as e:
        st.error(f"创建对话失败：{e.detail}")
        raise


def _load_messages(conv_id: str):
    """Load conversation history and populate chat_message list in session state."""
    try:
        msgs = request("GET", f"/api/conversations/{conv_id}/messages")
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
            conv_messages.append(entry)
        st.session_state["conv_messages"] = conv_messages
    except ApiError as e:
        logger.warning("Failed to load messages: %s", e)
        st.session_state["conv_messages"] = []


def _display_messages():
    """Render all messages from session state into Streamlit chat UI."""
    for m in st.session_state["conv_messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
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

                        if "token" in data:
                            full_answer += data["token"]
                            placeholder.markdown(full_answer + "▌")

                        if "sources" in data:
                            sources = data.get("sources", [])

        except httpx.TimeoutException:
            st.error("请求超时，请重试。")
            return
        except Exception as e:
            st.error(f"网络错误：{e}")
            return

        # Final render without cursor
        placeholder.markdown(full_answer)

        # Show sources
        if sources:
            source_lines = "\n".join(
                f"- 📄 {s.get('filename', 'Unknown')}"
                + (f" (p.{s['page']})" if s.get("page") else "")
                for s in sources
            )
            st.markdown("---\n**📎 来源文档**\n" + source_lines)

    # Save messages to state for next render (including sources)
    entry = {"role": "assistant", "content": full_answer}
    if sources:
        entry["sources"] = sources
    st.session_state["conv_messages"].append(entry)


def new_conversation():
    """Start a new conversation."""
    st.session_state["conv_id"] = None
    st.session_state["conv_messages"] = []