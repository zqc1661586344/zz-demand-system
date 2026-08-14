"""Workflow registry — maps workflow names to implementations."""

from app.workflows.base import BaseWorkflow


class WorkflowRegistry:
    """Singleton registry for workflow implementations."""

    _registry: dict[str, type[BaseWorkflow]] = {}

    @classmethod
    def register(cls, workflow_cls: type[BaseWorkflow]) -> type[BaseWorkflow]:
        """Register a workflow class (can be used as a decorator)."""
        name = workflow_cls.name
        if not name:
            name = workflow_cls.__name__
        cls._registry[name] = workflow_cls
        return workflow_cls

    @classmethod
    def get(cls, name: str) -> type[BaseWorkflow] | None:
        """Get a workflow class by name."""
        return cls._registry.get(name)

    @classmethod
    def list(cls) -> list[dict]:
        """List all registered workflows with metadata."""
        return [
            {"name": wf.name, "description": wf.description}
            for wf in cls._registry.values()
        ]

    @classmethod
    def execute(cls, name: str, ctx) -> any:
        """Execute a workflow by name."""
        wf_cls = cls.get(name)
        if wf_cls is None:
            raise ValueError(f"Unknown workflow: {name}")
        wf = wf_cls()
        return wf.execute(ctx)