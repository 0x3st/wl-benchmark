"""Artifact path validation.

Artifact paths come from the sandboxed child (model-adjacent); the
parent must never read or embed files outside the run directory — a
compromised child could otherwise point at arbitrary local files and
have them uploaded to the platform or embedded in the review PDF.
"""
from __future__ import annotations

import os


def run_artifacts(result: dict, run_dir: str) -> list:
    """Artifact paths that actually live inside run_dir."""
    root = os.path.realpath(os.path.abspath(run_dir))
    out = []
    for a in result.get("artifacts", []):
        try:
            p = os.path.realpath(os.path.abspath(a))
        except (TypeError, ValueError):
            continue
        if p == root or p.startswith(root + os.sep):
            out.append(a)
    return out
