"""Chat / RAG Q&A Gradio page."""

import json
import os.path

import gradio as gr
import httpx

from app.ui.api_client import BASE_URL, ApiError
from app.ui.auth_helpers import AuthState, make_api_client

# 头像文件路径 — Gradio 通过其自身的文件服务 API 读取这些路径。要换自己的图片，把图片放进 static/ 目录并改下面两行即可（支持 png/jpg/svg 等常见格式）。
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
# 用户头像（user）
_USER_AVATAR = os.path.abspath(os.path.join(_STATIC_DIR, "user.svg"))
# AI 头像（bot）
_BOT_AVATAR = os.path.abspath(os.path.join(_STATIC_DIR, "bot.svg"))


def _iter_sse_events(resp):
    """把 SSE 原始响应行解析为结构化事件 dict。忽略非 `data: ` 前缀的行；遇到 `[DONE]` 终止标记则正常结束流。"""
    for raw in resp.iter_lines():
        if not raw.startswith("data: "):
            continue
        data_str = raw[6:]  # strip the "data: " prefix
        if data_str == "[DONE]":
            return
        try:
            yield json.loads(data_str)
        except json.JSONDecodeError:
            continue


def _raise_for_status(resp):
    """非 2xx 响应是普通 JSON 而非 SSE 流——把它转成明确错误，避免被静默吞掉。"""
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:  # noqa: BLE001 - fall back to raw body on parse failure
        detail = resp.text
    raise gr.Error(f"请求失败（{resp.status_code}）：{detail}")


def _format_sources(sources: list) -> str:
    """把来源列表格式化成聊天气泡里显示的 Markdown 文本。"""
    if not sources:
        return ""
    lines = [
        f"- {s.get('filename', 'Unknown')}" + (f" (p.{s.get('page')})" if s.get("page") else "")
        for s in sources
    ]
    return "\n\n**📎 Sources:**\n" + "\n".join(lines)


def send_message_stream(message: str, history: list, conv_id: str, auth_json: str):
    """通过SSE流发送查询——token在聊天机器人中逐步显示。"""

    # Auto-create conversation on first message
    if not conv_id:
        client = make_api_client(auth_json)
        try:
            data = client.post("/api/conversations", json={})
            conv_id = data["id"]
        except ApiError as e:
            raise gr.Error(f"Failed to create conversation: {e.detail}")

    # Parse auth state for the Authorization header
    auth = AuthState.from_json(auth_json)

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    yield history, history, conv_id, ""  # Clear input, show user message

    assistant_msg = ""
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as http:
        try:
            with http.stream(
                "POST",
                f"/api/conversations/{conv_id}/query/stream",
                json={"query": message, "top_k": 5},
                headers={"Authorization": f"Bearer {auth.access_token}"},
            ) as resp:
                _raise_for_status(resp)
                for data in _iter_sse_events(resp):
                    if data.get("error"):  # backend reported a stream error
                        raise gr.Error(f"生成失败：{data['error']}")
                    if "token" in data:
                        assistant_msg += data["token"]
                        history[-1] = {"role": "assistant", "content": assistant_msg}
                        yield history, history, conv_id, ""
                    elif data.get("done"):
                        history[-1]["content"] += _format_sources(data.get("sources", []))
                        yield history, history, conv_id, ""
        except Exception as e:
            if not assistant_msg:
                history[-1]["content"] = f"> ⚠️ **出错了：** {e}"
                yield history, history, conv_id, ""


def render(auth_state: gr.State, msgs_state: gr.State, conv_id_state: gr.State) -> dict:
    """渲染聊天界面。使用app.py中的持久化状态。"""

    with gr.Column(scale=1):
        gr.Markdown("## 💬 问答", elem_classes="page-header")

        chatbot = gr.Chatbot(
            height=480,
            bubble_full_width=False,
            show_label=False,
            type="messages",
            elem_classes="chat-area",
            avatar_images=(_USER_AVATAR, _BOT_AVATAR),
        )

        with gr.Row(elem_classes="input-area"):
            msg_input = gr.Textbox(
                placeholder="输入您的问题...",
                scale=4,
                show_label=False,
                container=False,
            )
            send_btn = gr.Button("发送", variant="primary", scale=1)

        # send_message_stream writes progressively to the chatbot AND persistent state
        send_btn.click(
            fn=send_message_stream,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, msgs_state, conv_id_state, msg_input],
        )

        msg_input.submit(
            fn=send_message_stream,
            inputs=[msg_input, msgs_state, conv_id_state, auth_state],
            outputs=[chatbot, msgs_state, conv_id_state, msg_input],
        )

    return {
        "chatbot": chatbot,
        "msg_input": msg_input,
    }
