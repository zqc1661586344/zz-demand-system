"""Document review workflow example — a simple multi-step approval process."""

from app.workflows.base import BaseWorkflow, WorkflowContext
from app.workflows.registry import WorkflowRegistry


@WorkflowRegistry.register
class DocumentReviewWorkflow(BaseWorkflow):
    name = "document_review"
    description = "Document review workflow: submit → review → approve/reject"

    def get_steps(self) -> list[dict]:
        return [
            {"name": "submit", "description": "Submit document for review"},
            {"name": "review", "description": "Review document content"},
            {"name": "approve", "description": "Approve or reject the document"},
        ]

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        # Step 1: Submit
        ctx.step_results["submit"] = {"status": "completed", "document": ctx.input_data}

        # Step 2: Review (placeholder logic)
        ctx.step_results["review"] = {"status": "completed", "notes": "Reviewed by system"}

        # Step 3: Approve
        ctx.step_results["approve"] = {"status": "completed", "decision": "approved"}

        ctx.output_data = {
            "final_status": "approved",
            "steps": ctx.step_results,
        }
        return ctx

    def validate_input(self, input_data: dict | None) -> list[str]:
        errors = []
        if input_data is None:
            errors.append("input_data is required")
        elif "document_id" not in input_data:
            errors.append("document_id is required in input_data")
        return errors