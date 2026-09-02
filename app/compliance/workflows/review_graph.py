"""审查图构建（app/compliance/workflows/review_graph.py）—— LangGraph StateGraph。

按设计文档 §5.5.2 组装审查流水线：
    parse → supervise → extract → review ─┬─(template_id 有)→ compare → reflect
                                          └─(无)───────────→ reflect
    reflect ──(质量<阈值 且重试未超限)──→ review（自反思回炉）
           ├─(HITL 启用且有高风险)──→ human_review → generate_report
           └─(否则)────────────→ generate_report
    generate_report → END

节点由 compliance.harness.ComplianceHarness 提供（runtime.py）；本模块只负责
把节点、边、条件边组装成 graph。compile(checkpointer=...) 在 harness.__init__ 完成。

依赖注记：review_graph 顶层 import ComplianceHarness 不会触发循环依赖——
runtime.py 只在 ComplianceHarness.__init__ 方法内【延迟】导入 build_review_graph，
因此「review_graph → runtime（顶层无 review_graph 导入）→ 安全」。
"""

from langgraph.graph import END, StateGraph

from app.compliance.harness.runtime import ComplianceHarness
from app.compliance.workflows.state import ReviewState


def build_review_graph(harness: ComplianceHarness) -> StateGraph:
    """构建审查 StateGraph（含节点/边/条件边；返回未编译的图，由 harness compile）。"""
    g = StateGraph(ReviewState)

    # ---- 节点（全部由 harness 方法实现） ----
    g.add_node("parse", harness.parse_document)
    g.add_node("supervise", harness.supervise)
    g.add_node("extract", harness.extract_clauses)
    g.add_node("review", harness.review_clauses)
    g.add_node("reflect", harness.reflect)
    g.add_node("compare", harness.compare_template)
    g.add_node("human_review", harness.human_review)
    g.add_node("generate_report", harness.generate_report)

    # ---- 入口与主线 ----
    g.set_entry_point("parse")
    g.add_edge("parse", "supervise")
    g.add_edge("supervise", "extract")
    g.add_edge("extract", "review")

    # review → (有 template_id → compare) / (无 → reflect)
    g.add_conditional_edges(
        "review",
        harness.should_compare,
        {"compare": "compare", "skip": "reflect"},
    )
    g.add_edge("compare", "reflect")

    # reflect → (质量不达标且重试未超限 → 回 review) /
    #            (HITL 启用且有高风险 → human_review) / (否则 → generate_report)
    g.add_conditional_edges(
        "reflect",
        harness.should_retry,
        {"retry": "review", "human": "human_review", "skip_human": "generate_report"},
    )

    g.add_edge("human_review", "generate_report")
    g.add_edge("generate_report", END)

    return g