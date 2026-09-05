"""法规知识库 — 合规审查的法规依据来源（P0 已内置 4 部核心法规种子）。

后端 API     : app/compliance/api/knowledge.py
后端模型     : app/compliance/models/regulation.py
后端 Schema  : app/compliance/schemas/regulation.py
"""

import json

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import ApiError, delete, get, post

logger = get_logger(__name__)


def _list_regulations():
    try:
        data = get("/api/compliance/knowledge/regulations")
        if isinstance(data, dict):
            return data.get("items", [])
        return data if isinstance(data, list) else []
    except ApiError as e:
        st.error(f"获取法规列表失败：{e.detail}")
        return []


def _create_regulation(payload: dict):
    try:
        return post("/api/compliance/knowledge/regulations", json=payload)
    except ApiError as e:
        st.error(f"创建法规失败：{e.detail}")
        return None


def _delete_regulation(reg_id: str):
    try:
        return delete(f"/api/compliance/knowledge/regulations/{reg_id}")
    except ApiError as e:
        st.error(f"删除法规失败：{e.detail}")
        return None


def _seed_regulations():
    try:
        return post("/api/compliance/knowledge/seed", json={})
    except ApiError as e:
        st.error(f"初始化种子数据失败：{e.detail}")
        return None


def _search_regulations(query: str, top_k: int = 5):
    try:
        data = post(
            "/api/compliance/knowledge/search",
            json={"query": query, "top_k": top_k},
        )
        if isinstance(data, dict):
            return data.get("hits", [])
        return data if isinstance(data, list) else []
    except ApiError as e:
        st.error(f"检索失败：{e.detail}")
        return None


def page():
    st.markdown("## 📚 法规知识库")
    st.caption("合规审查的法规依据来源（P0 已内置 4 部核心法规种子）。")

    tab_list, tab_new, tab_search = st.tabs(["📜 法规列表", "➕ 录入法规", "🔎 法规检索"])

    with tab_list:
        _render_list()

    with tab_new:
        _render_form()

    with tab_search:
        _render_search()


def _type_label(t: str) -> str:
    return {
        "law": "法律",
        "regulation": "行政法规",
        "admin_regulation": "行政法规",
        "judicial_interpretation": "司法解释",
        "local_rule": "地方性法规",
        "local_regulation": "地方性法规",
    }.get(t, t or "law")


def _status_icon(s: str) -> str:
    return {"active": "🟢", "archived": "⚪", "deprecated": "🔴"}.get(s, "⚪")


def _render_list():
    c_seed, c_refresh = st.columns([1, 5])
    if c_seed.button("🌱 初始化种子数据", help="一键录入劳动合同方向 4 部核心法规（P0 种子）"):
        result = _seed_regulations()
        if result:
            if isinstance(result, dict):
                st.success(
                    f"✅ 已加载 {result.get('loaded', 0)} 条，跳过已存在 {result.get('skipped', 0)} 条"
                )
            else:
                st.success("✅ 种子数据已初始化")
            st.rerun()

    items = _list_regulations()
    if not items:
        st.info("法规库为空，请先点击『初始化种子数据』或手动录入。")
        return

    st.caption(f"共 {len(items)} 部法规")

    for reg in items:
        status = reg.get("status", "active")
        icon = _status_icon(status)
        reg_type = reg.get("regulation_type", "law")
        title = reg.get("title", "(未命名)")
        article_count = reg.get("article_count", 0)

        with st.expander(
            f"{icon} **{title}** — {_type_label(reg_type)}  (共 {article_count} 条)",
            expanded=False,
        ):
            source = reg.get("source")
            pub = reg.get("publish_date")
            eff = reg.get("effective_date")
            st.markdown(f"**发布**：`{pub or '—'}`  **生效**：`{eff or '—'}`  **状态**：{status}")
            if source:
                st.markdown(f"**来源**：{source}")

            c1, c2 = st.columns(2)
            if c2.button("🗑 删除", key=f"del_reg_{reg.get('id')}"):
                ok = _delete_regulation(reg.get("id"))
                if ok:
                    st.success("已删除")
                    st.rerun()


def _render_form():
    st.markdown("### 手动录入法规")
    with st.form("new_regulation"):
        title = st.text_input("法规名称 *", placeholder="如：中华人民共和国劳动合同法")
        reg_type = st.selectbox(
            "法规类型 *",
            options=["law", "admin_regulation", "judicial_interpretation", "local_rule", "other"],
            index=0,
        )

        c1, c2 = st.columns(2)
        publish_date = c1.date_input("发布日期", value=None)
        effective_date = c2.date_input("生效日期", value=None)

        source = st.text_input("来源", placeholder="官方网站 / 出版社 / 文号等")

        articles_text = st.text_area(
            "法规条款（JSON，可选）",
            placeholder='[{"article_number": "第19条", "content": "劳动合同期限三个月以上不满一年的..."}]',
            height=180,
        )

        submitted = st.form_submit_button("✅ 保存")
        if submitted:
            if not title.strip():
                st.error("法规名称必填")
                return

            articles = None
            if articles_text.strip():
                try:
                    parsed = json.loads(articles_text)
                    if isinstance(parsed, list):
                        articles = parsed
                    else:
                        st.error("条款 JSON 必须是数组")
                        return
                except json.JSONDecodeError as e:
                    st.error(f"条款 JSON 解析失败：{e}")
                    return

            payload = {
                "title": title.strip(),
                "regulation_type": reg_type,
                "publish_date": publish_date.isoformat() if publish_date else None,
                "effective_date": effective_date.isoformat() if effective_date else None,
                "source": source.strip() or None,
                "articles": articles,
            }
            result = _create_regulation(payload)
            if result:
                st.success(f"✅ 法规已录入：{result.get('title', title)}")
                st.rerun()


def _render_search():
    st.markdown("### 法规语义检索")
    st.caption("输入一段合同条款或描述，从法规库中检索匹配的法规依据（向量相似度）。")

    query = st.text_area("查询文本", placeholder="如：试用期最长不得超过多长时间？", height=80)
    top_k = st.slider("返回条数", min_value=3, max_value=20, value=5)

    if st.button("🔍 检索", disabled=not query.strip()):
        hits = _search_regulations(query.strip(), top_k=top_k)
        if hits is None:
            return
        if not hits:
            st.info("未检索到相关法规条款。")
            return

        st.caption(f"检索到 {len(hits)} 条结果")
        for i, r in enumerate(hits, 1):
            score = r.get("score", 0)
            title = r.get("regulation_title", "—")
            article = r.get("article_number", "")
            content = r.get("content", "—")

            with st.expander(
                f"**#{i}** 相似度 `{score:.3f}`  |  {title}  {article}",
                expanded=(i <= 2),
            ):
                st.markdown(f"> {content}")
                st.caption(f"article_id: {r.get('article_id', '—')}")
