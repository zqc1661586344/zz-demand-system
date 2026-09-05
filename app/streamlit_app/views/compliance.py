"""Compliance review page — 上传合同 → 触发审查 → 查看风险 → 人工审核。"""

import time
from datetime import datetime

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import ApiError, download_bytes, get, post

logger = get_logger(__name__)


def _fetch_documents():
    try:
        data = get("/api/documents")
        return data.get("items", []) if isinstance(data, dict) else (data or [])
    except ApiError as e:
        st.error(f"获取文档列表失败：{e.detail}")
        return []


def _fetch_reviews():
    try:
        return get("/api/compliance/reviews")
    except ApiError as e:
        st.error(f"获取审查列表失败：{e.detail}")
        return []


def _fetch_review_detail(review_id: str):
    try:
        return get(f"/api/compliance/reviews/{review_id}")
    except ApiError as e:
        st.error(f"获取审查详情失败：{e.detail}")
        return None


def _start_review(document_id: str, doc_type: str | None = None):
    payload = {"document_id": document_id}
    if doc_type:
        payload["doc_type"] = doc_type
    try:
        return post("/api/compliance/reviews", json=payload)
    except ApiError as e:
        st.error(f"触发审查失败：{e.detail}")
        return None


_RISK_COLOR = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_RISK_LABEL = {"high": "高风险", "medium": "中风险", "low": "低风险"}

_REPORT_FORMATS = [
    ("HTML", "html", "text/html"),
    (
        "Word (.docx)",
        "word",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("PDF", "pdf", "application/pdf"),
]


def _render_report_downloads(review_id: str):
    """渲染审查报告下载按钮组（P1）。"""
    st.markdown("### 📥 下载报告")
    st.caption("生成失败的格式会被跳过（如环境未装 weasyprint 则 PDF 无数据）。")
    cols = st.columns(len(_REPORT_FORMATS))
    for col, (label, fmt, mime) in zip(cols, _REPORT_FORMATS):
        with col:
            try:
                data = download_bytes(f"/api/compliance/reviews/{review_id}/report/{fmt}")
            except ApiError as e:
                st.button(f"⬇ {label}", disabled=True, help=str(e.detail))
                continue
            ts = datetime.now().strftime("%Y%m%d")
            st.download_button(
                label=f"⬇ {label}",
                data=data,
                file_name=f"compliance-review-{review_id[:8]}-{ts}.{fmt}",
                mime=mime,
                use_container_width=True,
                key=f"dl_{fmt}_{review_id[:8]}",
            )


def page():
    st.markdown("## ⚖️ 合规审查")

    tab_new, tab_list = st.tabs(["📝 发起审查", "📋 历史记录"])

    # ==============================================================
    # Tab 1: 发起审查
    # ==============================================================
    with tab_new:
        st.markdown("### 选择要审查的文档")

        docs = _fetch_documents()
        indexed_docs = [d for d in docs if d.get("status") == "indexed"]

        if not indexed_docs:
            st.info("暂无可审查的文档，请先到 **📁 文档管理** 上传文件并等待索引完成。")
        else:
            doc_options = {d["id"]: d.get("original_filename", d["id"]) for d in indexed_docs}
            selected_id = st.selectbox(
                "选择文档",
                options=list(doc_options.keys()),
                format_func=lambda x: doc_options[x],
                help="仅显示已索引完成的文档（status=indexed）",
            )

            doc_type = st.selectbox(
                "合同类型",
                options=[
                    "auto",
                    "labor_contract",
                    "sales_contract",
                    "nda",
                    "service_contract",
                    "other",
                ],
                index=0,
                help="auto 让系统自动判断；选 labor_contract 会加载劳动合同方向的 Playbook 规则",
            )
            effective_type = None if doc_type == "auto" else doc_type

            if st.button("🔍 开始审查", type="primary", use_container_width=True):
                with st.spinner("正在触发审查任务…"):
                    result = _start_review(selected_id, effective_type)
                if result:
                    review_id = result["review_id"]
                    st.success(f"✅ 审查已启动（review_id=`{review_id}`），正在后台处理…")
                    st.session_state["pending_review_id"] = review_id
                    st.rerun()

        # ---- 如果有正在进行/刚完成的审查，展示进度和结果 ----
        pending_id = st.session_state.get("pending_review_id")
        if pending_id:
            st.markdown("---")
            _render_review_result(pending_id, auto_refresh=True)

    # ==============================================================
    # Tab 2: 历史记录
    # ==============================================================
    with tab_list:
        st.markdown("### 审查历史")

        reviews_data = _fetch_reviews()
        items = reviews_data.get("items", []) if isinstance(reviews_data, dict) else reviews_data

        if not items:
            st.info("暂无审查记录。")
        else:
            summary_cols = st.columns([2, 1, 1, 1, 1, 2, 1])
            summary_cols[0].markdown("**审查 ID**")
            summary_cols[1].markdown("**状态**")
            summary_cols[2].markdown("**🔴 高**")
            summary_cols[3].markdown("**🟡 中**")
            summary_cols[4].markdown("**🟢 低**")
            summary_cols[5].markdown("**创建时间**")
            summary_cols[6].markdown("**操作**")

            for r in items:
                rid = r["review_id"]
                c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 1, 1, 1, 1, 2, 1])
                c1.caption(rid[:8] + "…")
                status = r.get("status", "")
                status_emoji = {
                    "completed": "✅",
                    "failed": "❌",
                    "pending": "⏳",
                    "parsing": "📄",
                    "reviewing": "🔍",
                    "generating": "📝",
                }.get(status, status)
                c2.markdown(f"{status_emoji} `{status}`")
                c3.markdown(f"🔴 {r.get('high_risk_count', 0)}")
                c4.markdown(f"🟡 {r.get('medium_risk_count', 0)}")
                c5.markdown(f"🟢 {r.get('low_risk_count', 0)}")
                c6.caption((r.get("created_at") or "")[:19])
                if c7.button("查看", key=f"view_{rid}"):
                    st.session_state["pending_review_id"] = rid
                    st.rerun()

            st.markdown("---")
            rid_input = st.text_input(
                "或输入 review_id 查看详情",
                placeholder="完整 review_id",
                key="rid_lookup",
            )
            if rid_input and st.button("查询", key="lookup_btn"):
                st.session_state["pending_review_id"] = rid_input.strip()
                st.rerun()

        # 展示选中的审查详情
        if st.session_state.get("pending_review_id"):
            st.markdown("---")
            _render_review_result(st.session_state["pending_review_id"], auto_refresh=False)


def _render_review_result(review_id: str, auto_refresh: bool = False):
    """渲染单个审查的完整结果：状态 → 汇总 → 风险列表 → 报告。"""

    detail = _fetch_review_detail(review_id)
    if detail is None:
        return

    status = detail.get("status", "")
    st.markdown(f"### 审查结果 `{review_id[:8]}…`")

    # ---- 状态条 ----
    status_info = {
        "completed": ("✅ 审查完成", "success"),
        "failed": ("❌ 审查失败", "error"),
        "pending": ("⏳ 排队中…", "info"),
        "parsing": ("📄 解析文档中…", "info"),
        "planning": ("📋 制定审查计划…", "info"),
        "reviewing": ("🔍 匹配 Playbook 规则…", "info"),
        "reflecting": ("🤔 自反思质量评估…", "info"),
        "pending_human": ("👤 等待人工审核（MVP 不阻塞）", "info"),
        "generating": ("📝 生成报告中…", "info"),
    }
    msg, level = status_info.get(status, (f"状态: {status}", "info"))
    getattr(st, level)(msg)

    if auto_refresh and status not in ("completed", "failed"):
        time.sleep(2)
        st.rerun()

    if status == "failed" and detail.get("error_message"):
        st.error(f"错误：{detail['error_message']}")
        return

    if status != "completed":
        return

    # ---- 风险汇总卡片 ----
    h, m, l = (
        detail.get("high_risk_count", 0),
        detail.get("medium_risk_count", 0),
        detail.get("low_risk_count", 0),
    )
    total = h + m + l

    col_h, col_m, col_l, col_doc = st.columns(4)
    col_h.metric("🔴 高风险", h)
    col_m.metric("🟡 中风险", m)
    col_l.metric("🟢 低风险", l)
    with col_doc:
        st.metric("📄 合同类型", detail.get("doc_type") or "未知")
        st.caption(f"总风险数: {total}")

    if total == 0:
        st.success("🎉 未检出风险条款，合同合规！")
        _render_report_downloads(review_id)
        return
    # ---- 风险明细 ----
    st.markdown("---")
    _render_report_downloads(review_id)
    st.markdown("---")
    st.markdown("### 风险明细")

    risks = detail.get("risks", [])
    for idx, risk in enumerate(risks, 1):
        level = risk.get("risk_level", "low")
        level_icon = _RISK_COLOR.get(level, "⚪")
        level_label = _RISK_LABEL.get(level, level)

        with st.expander(
            f"{level_icon} #{idx} [{level_label}] {risk.get('clause_number') or '—'} — {risk.get('description', '')[:60]}",
            expanded=(level == "high"),
        ):
            rc1, rc2 = st.columns([3, 1])
            with rc1:
                st.markdown(f"**风险描述**：{risk.get('description', '—')}")
                st.markdown(f"**风险类别**：`{risk.get('risk_category', '—')}`")
                if risk.get("suggestion"):
                    st.markdown(f"**修改建议**：{risk['suggestion']}")

                refs = risk.get("legal_references", [])
                if refs:
                    st.markdown("**法规依据**：")
                    for ref in refs:
                        verified = "✅" if ref.get("verified") else "⚠️ 需人工核实"
                        st.markdown(
                            f"  {verified} **{ref.get('ref_name', '—')}** {ref.get('ref_article', '')}  \n"
                            f"  > {ref.get('ref_content', '—')}"
                        )
                else:
                    st.caption("法规依据：无（法规知识库为空时降级显示）")

                if risk.get("human_confirmed"):
                    decision = risk.get("human_decision", "confirmed")
                    st.caption(f"👤 人工已处理：{decision}")

            with rc2:
                st.markdown(f"**置信度**：{risk.get('ai_confidence', '—')}")
                st.markdown(f"**条款号**：{risk.get('clause_number') or '—'}")

    # ---- 人工审核区 ----
    st.markdown("---")
    st.markdown("### 👤 人工审核（MVP 占位）")
    st.info(
        "人工审核接口已就绪（`POST /api/compliance/reviews/{id}/human-review`），前端批量操作界面可在 P1 补全。当前风险可在上方列表逐条查阅确认。"
    )
