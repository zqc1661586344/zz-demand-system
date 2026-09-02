"""人机协同管理器（app/compliance/harness/hitl.py）——HITL 决策与操作留痕。

设计文档 F07（P1 完整实现）：高风险项自动暂停（LangGraph interrupt），人工确认后
resume；全留痕（compliance_human_actions 表）。

MVP 阶段按计划采用**简化 HITL**：
  - `should_pause()`：compliance_hitl_enabled 且存在 high 风险时返回 True（决策入口）；
  - 审查图 human_review 节点调 `record_human_action()` 把人工操作写入留痕表 + 回写
    risk 的 human_confirmed / human_decision / human_note 字段；
  - 真正 `interrupt/resume`（暂停等待）放入 review_graph 条件分支，MVP 默认 human_review
    节点 = 记录后继续，不阻塞流水线（与计划「HITL 预留」一致）；
  - resume 指令构造保留在 `build_resume_command()`（P1 启用真正 interrupt 时用）。
"""

from datetime import datetime, timezone
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.compliance.models.report import ComplianceHumanAction
from app.compliance.models.review import ComplianceRisk
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class HitlManager:
    """人机协同决策与业务留痕。"""

    def should_pause(self, risks: list[dict]) -> bool:
        """是否需要在人工确认点暂停（HITL 启用且有高风险项）。

        MVP 决策基于风险等级：存在 risk_level == "high" 即需人工介入。
        """
        if not settings.compliance_hitl_enabled:
            return False
        return any(r.get("risk_level") == "high" for r in risks or [])

    def record_human_action(
        self,
        db: Session,
        review_id: str,
        action: str,
        risk_ids: list[str],
        operator_id: str | None = None,
        new_risk_level: Optional[str] = None,
        new_suggestion: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        """记录一条人工操作并回写相关风险项（全留痕）。

        Args:
            db: 数据库会话。
            review_id: 审查任务 id。
            action: confirm / modify_level / edit_suggestion / mark_false /
                    batch_confirm / resume。
            risk_ids: 涉及的风险项 id 列表。
            operator_id: 操作人（当前用户 id）。
            new_risk_level / new_suggestion / note: 操作内容（按 action 类型使用）。
        """
        for risk_id in risk_ids:
            risk = db.query(ComplianceRisk).filter(ComplianceRisk.id == risk_id).first()
            old_level = risk.risk_level if risk else None
            old_sugg = risk.suggestion if risk else None

            if risk:
                risk.human_confirmed = True
                risk.human_note = note
                if action in ("confirm", "batch_confirm"):
                    risk.human_decision = "confirmed"
                elif action == "mark_false":
                    risk.human_decision = "rejected"
                elif action == "modify_level" and new_risk_level:
                    risk.human_decision = "modified"
                    risk.risk_level = new_risk_level
                elif action == "edit_suggestion" and new_suggestion:
                    risk.human_decision = "modified"
                    risk.suggestion = new_suggestion

            # 留痕（old_value 记录修改前内容）
            entry = ComplianceHumanAction(
                id=str(uuid.uuid4()),
                review_id=review_id,
                risk_id=risk_id,
                action_type=action,
                old_value=(
                    f"level={old_level};suggestion={old_sugg or ''}" if old_level else None
                ),
                new_value=(
                    f"level={new_risk_level or old_level};suggestion={new_suggestion or ''}"
                    if action in ("modify_level", "edit_suggestion", "mark_false", "confirm")
                    else (note or None)
                ),
                operator_id=operator_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(entry)
        db.commit()
        logger.info(
            "human action recorded: review=%s action=%s risks=%d",
            review_id,
            action,
            len(risk_ids),
        )

    def build_resume_command(self, human_decisions: dict) -> dict:
        """构造 resume 指令（预留）。

        P1 启用真正 LangGraph interrupt 后，用 `Command(resume=...)` 恢复：
        返回 {"__interrupt_resume": human_decisions} —— 由 harness.resume_review 组装。
        MVP 未真正 interrupt，此函数保留为占位。
        """
        return {"__interrupt_resume": human_decisions}