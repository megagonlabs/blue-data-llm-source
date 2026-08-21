"""Backwards-compatible name for :class:`~blue_data_llm_source.source.LlmSource`.

This module existed when ``blue-data`` was an optional extra: the core matched the source
query surface by shape, and this subclass added the real inheritance for callers who wanted
it. ``blue-data`` is now a declared dependency and ``LlmSource`` inherits ``DataSource``
directly, so there is nothing left for a separate class to add.

``BlueDataLlmSource`` remains as an alias so an existing import keeps working. New code
should use ``LlmSource``.
"""

from __future__ import annotations

from blue_data_llm_source.source import LlmSource

# Not a subclass. A subclass would be a second class to test and a second MRO to get wrong,
# for no behaviour that LlmSource does not already have.
BlueDataLlmSource = LlmSource

__all__ = ["BlueDataLlmSource"]
