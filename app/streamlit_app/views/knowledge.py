"""法规知识库管理 — 法规上传、检索、种子数据初始化。"""

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import ApiError, delete, get, post

logger = get_logger(__name__)


def _list_regulations():
    try:
        return get("/api/compliance/knowledge/regulations")
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


def _render_list():
    c_seed, c_refresh = st.columns([1, 5])
    if c_seed.button("🌱 初始化种子数据", help="一键录入劳动合同方向 4 部核心法规（P0 种子）"):
        result = _seed_regulations()
        if result:
            st.success(f"✅ 已初始化 {len(result) if isinstance(result, list) else result} 条法规")
            st.rerun()

    items = _list_regulations()
    if not items:
        st.info("法规库为空，请先点击『初始化种子数据』或手动录入。")
        return

    for reg in items:
        status = reg.get("status", "active")
        status_icon = {"active": "🟢", "archived": "⚪", "deprecated": "🔴"}.get(status, "⚪")
        with st.expander(
            f"{status_icon} **{reg.get('name', '(未命名)')}** — {reg.get('regulation_type', 'law')} "
            f"(共 {reg.get('clause_count', 0)} 条)",
            expanded=False,
        ):
            st.markdown(f"**描述**：{reg.get('description') or '—'}")
            st.markdown(
                f"**发布日期**：{reg.get('published_date') or '—'}  "
                f"**生效日期**：{reg.get('effective_date') or '—'}  "
                f"**状态**：{status}"
            )
            if reg.get("source_url"):
                st.markdown(f"**来源**：{reg['source_url']}")
            c1, c2 = st.columns(2)
            if c2.button("🗑 删除", key=f"del_reg_{reg.get('id')}"):
                ok = _delete_regulation(reg.get("id"))
                if ok:
                    st.success("已删除")
                    st.rerun()


def _render_form():
    st.markdown("### 手动录入法规（基础字段）")
    with st.form("new_regulation"):
        name = st.text_input("法规名称 *", placeholder="如：中华人民共和国劳动合同法")
        reg_type = st.selectbox(
            "法规类型",
            options=["law", "regulation", "judicial_interpretation", "local_rule", "other"],
            index=0,
        )
        effective_date = st.date_input("生效日期", value=None)
        description = st.text_area("法规描述", placeholder="简要说明该法规的适用范围和核心内容")
        clauses_text = st.text_area(
            "法规条款（JSON，可选）",
            placeholder='[{"article": "第19条", "content": "劳动合同期限三个月以上不满一年的..."}]',
            height=150,
        )
        submitted = st.form_submit_button("保存")
        if submitted:
            if not name:
                st.error("法规名称必填")
                return
            import json

            clauses = []
            if clauses_text.strip():
                try:
                    clauses = json.loads(clauses_text)
                except json.JSONDecodeError as e:
                    st.error(f"条款 JSON 解析失败：{e}")
                    return

            payload = {
                "name": name,
                "regulation_type": reg_type,
                "effective_date": effective_date.isoformat() if effective_date else None,
                "description": description,
                "clauses": clauses,
            }
            result = _create_regulation(payload)
            if result:
                st.success("✅ 法规已录入")
                st.rerun()


def _render_search():
    st.markdown("### 法规语义检索")
    st.caption("输入一段合同条款或描述，从法规库中检索匹配的法规依据。")
    query = st.text_area("查询文本", placeholder="如：试用期最长不得超过多长时间？", height=80)
    top_k = st.slider("返回条数", min_value=3, max_value=20, value=5)
    if st.button("🔍 检索", disabled=not query.strip()):
        try:
            results = post(
                "/api/compliance/knowledge/search",
                json={"query": query, "top_k": top_k},
            )
        except ApiError as e:
            st.error(f"检索失败：{e.detail}")
            return
        if not results:
            st.info("未检索到相关法规条款。")
            return
        for i, r in enumerate(results, 1):
            score = r.get("score", 0)
            st.markdown(
                f"**#{i} [{score:.3f}] {r.get('regulation_name', '—')} {r.get('article', '')}**"
            )
            st.markdown(f"> {r.get('content', '—')}")
