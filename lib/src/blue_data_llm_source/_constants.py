"""Vocabulary constants: protocol, variant, op codes, and the virtual hierarchy."""

from __future__ import annotations

# The data-source design doc specifies protocol as the family and variant as the
# implementation within it, the same way sql/postgres and sql/mysql are specified.
PROTOCOL = "llm"
VARIANT_OPENAI = "openai"
DEFAULT_VARIANT = VARIANT_OPENAI

# Op codes accepted by execute_query when it is handed a dict.
OP_FETCH = "LLM.FETCH"
OP_MODELS = "LLM.MODELS"
# What a fetch of the same arguments could cost at most, without contacting the provider.
# An op rather than only a method so a caller reaching this source through the registry can
# ask the question too, which is where a planner sizing work actually stands.
OP_ESTIMATE = "LLM.ESTIMATE"
OPS = (OP_FETCH, OP_MODELS, OP_ESTIMATE)

# The virtual hierarchy the registry walks. An LLM has no databases or collections,
# so one of each is synthesised, named for the family rather than the vendor.
DATABASE = "llm"
COLLECTION = "public"
ENTITY = "llm_entity"
ENTITY_ATTRIBUTE = "llm_entity_attribute"
ENTITY_ATTRIBUTE_TYPE = "text"

# The property under which a fetch hands its budget to a budget-aware client. Passed per
# call rather than set on the client, because one client can serve several concurrent
# fetches and an attribute would let them overwrite each other's ceiling.
BUDGET_PROPERTY = "budget"

# The property under which a fetch hands a client a list to append this call's Usage to.
# Per call, so concurrent fetches through one client cannot read each other's entries.
USAGE_SINK_PROPERTY = "usage_sink"
