"""Chat handler — SSE streaming, conversation management, source display."""

import json
import logging

import chainlit as cl
import httpx

from app.chainlit_app.api_client import BASE_URL, ApiError

logger = logging.getLogger(__name__)

# Number of initial top-k documents to retrieve for each query
TOP_K = 5


async def _ensure_conversation() -> str:
    """Get or create the current conversation ID from user session."""
    import chainlit as cl

    conv_id = cl.user_session.get("conv_id")
    if conv_id:
        return conv_id

    from app.chainlit_app.auth import get_api_client

    client = await get_api_client()
    try:
        data = await client.post("/api/conversations", json={})
        conv_id = data["id"]
        cl.user_session.set("conv_id", conv_id)
        return conv_id
    except ApiError as e:
        await cl.ErrorMessage(content=f"创建对话失败：{e.detail}").send()
        raise


async def handle_query(message_content: str):
    """Core SSE streaming handler.

    Streams tokens from the backend RAG endpoint into a Chainlit message,
    then appends sources as an expandable cl.Step.
    """
    import chainlit as cl

    conv_id = await _ensure_conversation()
    access_token = cl.user_session.get("access_token")

    msg = cl.Message(content="")
    sources = []
    full_answer = ""

    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as http:
            async with http.stream(
                "POST",
                f"/api/conversations/{conv_id}/query/stream",
                json={"query": message_content, "top_k": TOP_K},
                headers={"Authorization": f"Bearer {access_token}"},
            ) as resp:
                # Check for non-2xx response (not an SSE stream)
                if resp.status_code >= 400:
                    try:
                        body = await resp.aread()
                        detail = json.loads(body).get("detail", body.decode())
                    except Exception:
                        detail = await resp.aread()
                        detail = detail.decode()
                    await cl.ErrorMessage(
                        content=f"请求失败（{resp.status_code}）：{detail}"
                    ).send()
                    return

                # Parse SSE events
                async for raw in resp.aiter_lines():
                    if not raw.startswith("data: "):
                        continue
                    data_str = raw[6:]  # strip "data: " prefix
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if data.get("error"):
                        await cl.ErrorMessage(
                            content=f"生成失败：{data['error']}"
                        ).send()
                        return

                    if "token" in data:
                        full_answer += data["token"]
                        await msg.stream_token(data["token"])

                    if "sources" in data:
                        sources = data.get("sources", [])
                        # Also capture full_answer from the final event
                        if data.get("full_answer") and not full_answer:
                            full_answer = data["full_answer"]

    except httpx.TimeoutException:
        await cl.ErrorMessage(content="请求超时，请重试。").send()
        return
    except Exception as e:
        await cl.ErrorMessage(content=f"网络错误：{e}").send()
        return

    await msg.send()

    # Show sources as an expandable step if there are any
    if sources:
        step = cl.Step(name="📎 来源文档", root=False)
        for s in sources:
            line = f"- {s.get('filename', 'Unknown')}"
            if s.get("page"):
                line += f" (p.{s['page']})"
            await step.stream_token(line + "\n")
        await step.send()


async def handle_new_conversation():
    """Clear current conversation ID so the next message starts a fresh conversation."""
    import chainlit as cl

    cl.user_session.set("conv_id", None)
    await cl.Message(content="已创建新对话，请提问。").send()