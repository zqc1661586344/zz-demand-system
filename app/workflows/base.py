"""Abstract workflow base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class WorkflowContext:
    """Runtime context passed through workflow steps."""
    instance_id: str
    definition_id: str
    initiated_by: str
    input_data: dict | None = None
    output_data: dict | None = None
    step_results: dict = field(default_factory=dict)


class BaseWorkflow(ABC):
    """Abstract base class that all workflow implementations must extend."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        """Execute the full workflow and return the updated context."""
        ...

    @abstractmethod
    def get_steps(self) -> list[dict]:
        """Return the list of step definitions for display purposes.

        Each step dict: {"name": str, "description": str}
        """
        ...

    def validate_input(self, input_data: dict | None) -> list[str]:
        """Validate input data and return a list of error messages (empty = valid)."""
        return []