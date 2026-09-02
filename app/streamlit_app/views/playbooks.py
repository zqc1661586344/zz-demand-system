"""Playbook 规则管理 — 企业红线条款/标准规则的增删改查。"""

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import ApiError, delete, get, post, put

logger = get_logger(__name__)


def _list_playbooks():
    try:
        return get("/api/compliance/playbooks")
    except ApiError as e:
        st.error(f"获取 Playbook 列表失败：{e.detail}")
        return []


def _create_playbook(payload: dict):
    try:
        return post("/api/compliance/playbooks", json=payload)
    except ApiError as e:
        st.error(f"创建 Playbook 失败：{e.detail}")
        return None


def _update_playbook(pb_id: str, payload: dict):
    try:
        return put(f"/api/compliance/playbooks/{pb_id}", json=payload)
    except ApiError as e:
        st.error(f"更新 Playbook 失败：{e.detail}")
        return None


def _delete_playbook(pb_id: str):
    try:
        return delete(f"/api/compliance/playbooks/{pb_id}")
    except ApiError as e:
        st.error(f"删除 Playbook 失败：{e.detail}")
        return None


def page():
    st.markdown("## 📋 Playbook 规则库")
    st.caption("企业红线条款、标准措辞、风险阈值的可视化配置（P1）。")

    tab_list, tab_new = st.tabs(["📜 规则列表", "➕ 新建规则"])

    with tab_list:
        _render_list()

    with tab_new:
        _render_form()


def _render_list():
    items = _list_playbooks()
    if not items:
        st.info("暂未配置 Playbook 规则，请在右侧新建。")
        return

    for pb in items:
        active = pb.get("is_active", True)
        badge = "🟢" if active else "⚪"
        with st.expander(
            f"{badge} **{pb.get('name', '(未命名)')}** — {pb.get('contract_type', 'auto')} "
            f"({len(pb.get('rules', []))} 条规则)",
            expanded=False,
        ):
            st.markdown(f"**描述**：{pb.get('description') or '—'}")
            st.markdown(
                f"**版本**：`{pb.get('version', '1.0')}`  **状态**：{'启用' if active else '停用'}"
            )

            rules = pb.get("rules", []) or []
            if rules:
                st.markdown("**规则明细**：")
                for i, rule in enumerate(rules, 1):
                    level = rule.get("risk_level", "medium")
                    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(level, "⚪")
                    st.markdown(
                        f"{icon} `#{rule.get('rule_id', i)}` [{rule.get('rule_type', 'keyword')}] "
                        f"{rule.get('description', '—')}"
                    )
                    if rule.get("keywords"):
                        st.caption(f"关键词：{', '.join(rule['keywords'])}")
            else:
                st.caption("（无规则）")

            c1, c2 = st.columns(2)
            if c2.button("🗑 删除", key=f"del_{pb.get('id')}"):
                ok = _delete_playbook(pb.get("id"))
                if ok:
                    st.success("已删除")
                    st.rerun()
