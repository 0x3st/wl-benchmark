"""Task registry: maps task_type -> loader + runner class."""
from __future__ import annotations

import os
from typing import List, Optional

from .base import BaseTask, TaskResult
from .essay import EssayTask, select_rubric, discover as discover_essay
from .svg import SvgTask, discover as discover_svg
from .scheduling import SchedulingTask, discover as discover_scheduling
from .quant import QuantTask, discover as discover_quant

TASK_TYPES = ("essay", "quant", "scheduling", "svg")


def build_tasks(tasks_data_root: str, run_cfg: dict,
                artifacts_root: str,
                only_types: Optional[List[str]] = None) -> List[BaseTask]:
    """Instantiate all tasks found under tasks_data/."""
    tasks: List[BaseTask] = []
    types = only_types or TASK_TYPES

    if "essay" in types:
        modality = run_cfg.get("rubric_modality", "auto")
        for spec in discover_essay(os.path.join(tasks_data_root, "essay")):
            spec["rubric"] = select_rubric(spec, modality)
            tasks.append(EssayTask(spec, run_cfg, artifacts_root))

    if "svg" in types:
        for spec in discover_svg(os.path.join(tasks_data_root, "svg")):
            tasks.append(SvgTask(spec, run_cfg, artifacts_root))

    if "quant" in types:
        for spec in discover_quant(os.path.join(tasks_data_root, "quant")):
            tasks.append(QuantTask(spec, run_cfg, artifacts_root))

    if "scheduling" in types:
        for spec in discover_scheduling(
                os.path.join(tasks_data_root, "scheduling")):
            tasks.append(SchedulingTask(spec, run_cfg, artifacts_root))

    return tasks


__all__ = ["BaseTask", "TaskResult", "EssayTask", "SvgTask",
           "SchedulingTask", "QuantTask", "TASK_TYPES", "build_tasks"]
