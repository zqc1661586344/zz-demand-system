"""SSE 流式输出辅助（app/compliance/harness/stream.py）。

与现有 app/api/conversations.py 的 SSE 格式保持一致：单行 `data: {json}\n\n`
（无 event: 字段），前端 Streamlit 按 `data: ` 前缀逐行解析（复用 chat.py 模式）。

MVP 审查进度走「DB 轮询生成器」：SSE 端点每 500ms 查 compliance_reviews.status 差异，
用本模块把状态/风险事件格式化成 `data: {json}` 行对外推送。LangGraph astream 已由
harness/runtime 收敛到 DB 状态，本模块只负责把进度转成可推送的事件。

事件类型约定（前端 compliance_review.py 解析）：\n    {"type": "phase", "status": ..., "progress": int, "message": ...}\n    {"type": "risk", "risk": {...}}\n    {"type": "done", "status": "completed"}\n"""
import json


def format_event(payload: dict) -> str:
    """把事件 dict 格式化成 SSE `data: {json}\\n\\n` 单行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


def phase_data(status: str, progress: int, message: str) -> dict:
    """构造 phase 事件：status（阶段）、progress（0~100）、message（进度说明）。"""
    return {"type": "phase", "status": status, "progress": progress, "message": message}


def risk_data(risk: dict) -> dict:
    """构造 risk 事件：风险项（含 clause_number/risk_level/description）。"""
    return {
        "type": "risk",
        "risk": {
            "id": risk.get("id"),
            "clause_number": risk.get("clause_number"),
            "risk_level": risk.get("risk_level"),
            "risk_category": risk.get("risk_category"),
            "description": risk.get("description"),
        },
    }


def done_data() -> dict:
    """构造 done 事件：审查完成。"""
    return {"type": "done", "status": "completed"}


def error_data(message: str) -> dict:
    """构造 error 事件：审查失败。"""
    return {"type": "error", "status": "failed", "message": message}