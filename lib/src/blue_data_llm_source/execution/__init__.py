"""Running a plan's leaves against the model.

Execution consumes a plan and produces records. It decides nothing about how the ask was
split; what it owns is how many times one leaf may be asked and how a short answer is
reported.
"""

from __future__ import annotations

from blue_data_llm_source.execution.mapper import LeafRun, run_leaf

__all__ = ["LeafRun", "run_leaf"]
