"""QA escalation workflow example — route complex questions to experts."""

from app.workflows.base import BaseWorkflow, WorkflowContext
from app.workflows.registry import WorkflowRegistry


@WorkflowRegistry.register
class QAEscalationWorkflow(BaseWorkflow):
    name = "qa_escalation"
    description = "QA escalation: auto-answer → escalate to expert → resolve"

    def get_steps(self) -> list[dict]:
        return [
            {"name": "classify", "description": "Classify question complexity"},
            {"name": "auto_answer", "description": "Attempt automatic answer"},
            {"name": "escalate", "description": "Escalate to human expert if needed"},
            {"name": "resolve", "description": "Mark as resolved"},
        ]

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        ctx.step_results["classify"] = {"status": "completed", "complexity": "simple"}

        ctx.step_results["auto_answer"] = {
            "status": "completed",
            "answer": "Auto-generated answer based on knowledge base",
        }

        ctx.step_results["escalate"] = {"status": "skipped", "reason": "Auto-answered successfully"}

        ctx.step_results["resolve"] = {"status": "completed", "resolution": "answered"}

        ctx.output_data = {
            "final_status": "resolved",
            "steps": ctx.step_results,
        }
        return ctx