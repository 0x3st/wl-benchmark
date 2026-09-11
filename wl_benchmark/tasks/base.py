"""Task base classes and result containers."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskResult:
    task_id: str
    task_type: str
    model: str
    provider: str
    score: Optional[float] = None   # None = not auto-scorable, human review (essay/svg)
    max_score: float = 1.0
    detail: Dict[str, Any] = field(default_factory=dict)   # per-item breakdown
    artifacts: List[str] = field(default_factory=list)     # saved file paths
    latency: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "task_type": self.task_type,
            "model": self.model, "provider": self.provider,
            "score": (round(self.score, 4)
                      if self.score is not None else None),
            "max_score": self.max_score,
            "detail": self.detail, "artifacts": self.artifacts,
            "latency": round(self.latency, 2), "usage": self.usage,
            "error": self.error,
        }

    # ------------------------------------------------------------- context
    def load_text(self) -> str:
        """Full text of the first markdown artifact (for essay chaining)."""
        for p in self.artifacts:
            if p.endswith(".md") and os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read()
        return ""


class BaseTask(ABC):
    """A benchmark task: build prompt(s) -> run against model -> collect."""

    task_type: str = "base"

    def __init__(self, spec: Dict[str, Any], run_cfg: Dict[str, Any],
                 artifacts_dir: str):
        self.spec = spec
        self.run_cfg = run_cfg
        self.artifacts_dir = artifacts_dir

    @property
    def task_id(self) -> str:
        return self.spec.get("id", "unnamed")

    @abstractmethod
    def run(self, client, model: str,
            context: Optional[Dict[str, "TaskResult"]] = None) -> TaskResult:
        """Execute the task against one model.

        context: results of previously finished tasks in this run
                 (keyed by task_id), for chained tasks e.g. research proposal.
        client: wl_benchmark.client.ChatClient
        """
        raise NotImplementedError
