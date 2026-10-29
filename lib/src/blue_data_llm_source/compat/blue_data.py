"""Compatibility alias for :class:`~blue_data_llm_source.source.LlmSource`.

``LlmSource`` inherits ``DataSource`` directly. Existing
``BlueDataLlmSource`` imports still work; new code should use ``LlmSource``.
"""

from __future__ import annotations

from blue_data_llm_source.source import LlmSource

# Preserve class identity for callers of either name.
BlueDataLlmSource = LlmSource

__all__ = ["BlueDataLlmSource"]
