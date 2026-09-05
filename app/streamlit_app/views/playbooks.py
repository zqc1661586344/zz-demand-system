"""Playbook 规则管理 — 企业红线条款/标准规则的增删改查。

后端模型: app/compliance/models/playbook.py  （单条规则）
响应模型: app/compliance/schemas/playbook.py
API      : GET/POST /api/compliance/playbooks + GET/PUT/DELETE /{id} + POST /{id}/toggle
"""

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import ApiError, delete, get, post, put

logger = get_logger(__name__)


def _list_playbooks():
    try:
        data = get("/api/compliance/playbooks")
        if isinstance(data, dict):
            return data.get("items", [])
        return data if isinstance(data, list) else []
    except ApiError as e:
        st.error(f"获取 Playbook 列表失败：{e.detail}")
        return []


def _create_playbook(payload: dict):
    try:
        return post("/api/compliance/playbooks", json=payload)
    except ApiError as e:
        st.error(f"创建 Playbook 失败：{e.detail}")
        return None


def _delete_playbook(pb_id: str):
    try:
        return delete(f"/api/compliance/playbooks/{pb_id}")
    except ApiError as e:
        st.error(f"删除 Playbook 失败：{e.detail}")
        return None


def page():
    st.markdown("## 📋 Playbook 规则库")
    st.caption("企业红线条款、标准措辞、风险阈值的可视化配置。每条规则 = 一个审查判定点。")

    tab_list, tab_new = st.tabs(["📜 规则列表", "➕ 新建规则"])

    with tab_list:
        _render_list()

    with tab_new:
        _render_form()


def _level_icon(level: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(level, "⚪")


def _match_label(match_type: str) -> str:
    return {"keyword": "关键词", "semantic": "语义向量", "hybrid": "语义+LLM"}.get(
        match_type, match_type or "keyword"
    )


def _render_list():
    items = _list_playbooks()
    if not items:
        st.info("暂未配置 Playbook 规则，请在右侧新建。")
        return

    st.caption(f"共 {len(items)} 条规则")

    for pb in items:
        active = pb.get("is_active", True)
        badge = "🟢" if active else "⚪"
        level = pb.get("risk_level", "medium")
        icon = _level_icon(level)
        contract = pb.get("contract_type", "auto")
        clause = pb.get("clause_type") or "不限"
        match_t = pb.get("match_type", "keyword")

        header = (
            f"{badge} {icon} **{pb.get('name', '(未命名)')}** "
            f"`{contract}` / `{clause}`  "
            f"— [{_match_label(match_t)}]"
        )
        if pb.get("red_line"):
            header += "  🔴红线"

        with st.expander(header, expanded=False):
            st.markdown(f"**描述**：{pb.get('description') or '—'}")
            st.markdown(
                f"**版本**：`{pb.get('version', 1)}`  "
                f"**状态**：{'启用' if active else '停用'}  "
                f"**优先级**：`{pb.get('priority', 100)}`"
            )
            st.markdown(f"**风险等级**：{icon} `{level}`  ")
            st.markdown(f"**匹配方式**：`{match_t}`  阈值：`{pb.get('match_threshold', 0.8)}`")

            pattern = pb.get("match_pattern")
            if pattern:
                st.markdown("**匹配内容**：")
                st.code(pattern, language=None)

            st.markdown(
                f"**红线条款**：{'是 ✅' if pb.get('red_line') else '否'}  "
                f"**可谈判**：{'是' if pb.get('negotiable', True) else '否'}"
            )

            basis = pb.get("legal_basis_ref")
            if basis:
                st.markdown(f"**法规依据**：{basis}")

            pos = pb.get("standard_position")
            if pos:
                st.markdown(f"**企业立场**：{pos}")

            sug = pb.get("suggested_clause")
            if sug:
                st.markdown("**建议措辞**：")
                st.code(sug, language=None)

            c1, c2 = st.columns(2)
            if c2.button("🗑 删除", key=f"del_{pb.get('id')}"):
                ok = _delete_playbook(pb.get("id"))
                if ok:
                    st.success("已删除")
                    st.rerun()


def _render_form():
    with st.form("playbook_create", clear_on_submit=True):
        st.markdown("### 新建审查规则")

        c1, c2 = st.columns(2)
        name = c1.text_input("规则名称 *", placeholder="例如：试用期期限检查")
        contract_type = c2.selectbox(
            "合同类型 *",
            ["labor_contract", "nda", "procurement", "service_agreement", "other"],
            index=0,
        )

        description = st.text_area("规则描述", placeholder="简要说明这条规则要检查什么")

        c1, c2, c3 = st.columns(3)
        clause_type = c1.text_input(
            "适用条款类型", placeholder="parties / term / payment / 留空不限"
        )
        risk_level = c2.selectbox("风险等级 *", ["high", "medium", "low"], index=1)
        match_type = c3.selectbox("匹配方式 *", ["keyword", "semantic", "hybrid"], index=0)

        match_pattern = st.text_area(
            "匹配关键词 / 正则 *",
            placeholder='多个关键词用逗号分隔，例如："违约金,赔偿,damage"',
        )
        match_threshold = st.slider(
            "语义匹配阈值（semantic/hybrid 生效）",
            min_value=0.3,
            max_value=0.99,
            value=0.8,
            step=0.05,
        )

        legal_basis_ref = st.text_input("法规依据", placeholder="例如：劳动合同法第十九条")
        standard_position = st.text_area(
            "企业标准立场", placeholder="超过 N 个月的试用期需要额外审批"
        )
        suggested_clause = st.text_area("建议条款措辞", placeholder="建议替换成的标准条款文本")

        c1, c2, c3 = st.columns(3)
        red_line = c1.checkbox("🔴 红线条款（必须修改）", value=False)
        negotiable = c2.checkbox("可谈判", value=True)
        priority = c3.number_input(
            "优先级（数字越小越先检查）", min_value=1, max_value=999, value=100, step=10
        )

        submitted = st.form_submit_button("✅ 创建规则")
        if submitted:
            if not name.strip() or not match_pattern.strip():
                st.error("请填写规则名称和匹配关键词")
                return

            payload = {
                "name": name.strip(),
                "description": description.strip() or None,
                "contract_type": contract_type,
                "clause_type": clause_type.strip() or None,
                "risk_level": risk_level,
                "match_type": match_type,
                "match_pattern": match_pattern.strip(),
                "match_threshold": match_threshold,
                "legal_basis_ref": legal_basis_ref.strip() or None,
                "standard_position": standard_position.strip() or None,
                "suggested_clause": suggested_clause.strip() or None,
                "red_line": red_line,
                "negotiable": negotiable,
                "priority": priority,
            }
            result = _create_playbook(payload)
            if result:
                st.success(
                    f"规则已创建：{result.get('name', name)} (id={result.get('id', '')[:8]}...)"
                )
                st.rerun()
