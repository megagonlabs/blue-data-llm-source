"""What the client will and will not forward to the provider.

These exercise ``_call_kwargs`` directly. The question is what the provider would have
been handed, and answering it does not need a call. Reaching past the public surface is
the point of the file, so the private-access rule is relaxed here and nowhere else.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

from blue_data_llm_source.llm.client import DEFAULT_MODEL, DefaultLlmClient
from blue_data_llm_source.types import ConfigError

CONTROL_KEYS = ["cache", "cache_dir", "max_parallel"]
RESERVED_KEYS = ["api", "service_url", "variant", "protocol"]


@pytest.fixture
def client() -> DefaultLlmClient:
    return DefaultLlmClient()


def test_the_prompt_is_sent_as_a_single_user_message(client: DefaultLlmClient) -> None:
    kwargs = client._call_kwargs("who is the president?", {})
    assert kwargs["messages"] == [{"role": "user", "content": "who is the president?"}]


def test_an_unknown_llm_override_raises_config_error(client: DefaultLlmClient) -> None:
    with pytest.raises(ConfigError) as caught:
        client._call_kwargs("who?", {"llm.top_p": 0.4})
    assert "llm.top_p" in str(caught.value)


def test_the_rejection_names_the_accepted_override_set(client: DefaultLlmClient) -> None:
    with pytest.raises(ConfigError) as caught:
        client._call_kwargs("who?", {"llm.top_p": 0.4})
    message = str(caught.value)
    for accepted in ("llm.model", "llm.temperature", "llm.max_tokens", "llm.timeout"):
        assert accepted in message


def test_accepted_overrides_are_forwarded_without_their_prefix(
    client: DefaultLlmClient,
) -> None:
    kwargs = client._call_kwargs(
        "who?",
        {"llm.model": "gpt-4o-mini", "llm.temperature": 0.0, "llm.max_tokens": 256},
    )
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 256


@pytest.mark.parametrize("control", CONTROL_KEYS)
def test_a_control_key_never_reaches_the_provider_kwargs(
    client: DefaultLlmClient, control: str
) -> None:
    kwargs = client._call_kwargs("who?", {f"llm.{control}": "value"})
    assert control not in kwargs
    assert f"llm.{control}" not in kwargs


def test_every_control_key_at_once_still_leaves_only_the_call_fields(
    client: DefaultLlmClient,
) -> None:
    properties = {f"llm.{key}": 1 for key in CONTROL_KEYS}
    kwargs = client._call_kwargs("who?", properties)
    assert set(kwargs) == {"messages", "model", "reasoning_effort", "custom_llm_provider"}


@pytest.mark.parametrize("reserved", RESERVED_KEYS)
def test_a_reserved_field_cannot_be_forwarded_as_an_override(
    client: DefaultLlmClient, reserved: str
) -> None:
    with pytest.raises(ConfigError, match=f"llm.{reserved}"):
        client._call_kwargs("who?", {f"llm.{reserved}": "smuggled"})


@pytest.mark.parametrize("reserved", RESERVED_KEYS)
def test_a_reserved_field_in_plain_properties_is_not_forwarded(
    client: DefaultLlmClient, reserved: str
) -> None:
    kwargs = client._call_kwargs("who?", {reserved: "smuggled"})
    assert reserved not in kwargs


def test_connection_properties_are_not_forwarded(client: DefaultLlmClient) -> None:
    properties = {"connection": {"protocol": "llm", "variant": "openai"}, "name": "llm"}
    kwargs = client._call_kwargs("who?", properties)
    assert set(kwargs) == {"messages", "model", "reasoning_effort", "custom_llm_provider"}


def test_a_missing_model_falls_back_to_the_default(client: DefaultLlmClient) -> None:
    kwargs = client._call_kwargs("who?", {})
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["reasoning_effort"] == "medium"


def test_default_output_allowance_uses_the_reasoning_model_parameter(
    client: DefaultLlmClient,
) -> None:
    kwargs = client._call_kwargs("who?", {"llm.max_tokens": 25_000})
    assert kwargs["max_completion_tokens"] == 25_000
    assert "max_tokens" not in kwargs


def test_a_dated_gpt_6_luna_variant_uses_the_same_request_contract(
    client: DefaultLlmClient,
) -> None:
    kwargs = client._call_kwargs(
        "who?", {"llm.model": "gpt-6-luna-2026-09-22", "llm.max_tokens": 512}
    )
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["max_completion_tokens"] == 512
    assert "temperature" not in kwargs


def test_default_medium_reasoning_refuses_sampling_temperature(
    client: DefaultLlmClient,
) -> None:
    with pytest.raises(ConfigError, match="temperature.*reasoning_effort"):
        client._call_kwargs("who?", {"llm.temperature": 0})


def test_gpt_4o_keeps_sampling_and_legacy_output_parameter(
    client: DefaultLlmClient,
) -> None:
    kwargs = client._call_kwargs("who?", {"llm.model": "gpt-4o", "llm.max_tokens": 512})
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 512
    assert "max_completion_tokens" not in kwargs
    assert "reasoning_effort" not in kwargs


def test_gpt_4o_omits_a_null_reasoning_setting(client: DefaultLlmClient) -> None:
    kwargs = client._call_kwargs("who?", {"llm.model": "gpt-4o", "llm.reasoning_effort": None})
    assert kwargs["temperature"] == 0
    assert "reasoning_effort" not in kwargs


def test_gpt_6_luna_omits_a_null_temperature_setting(client: DefaultLlmClient) -> None:
    kwargs = client._call_kwargs("who?", {"llm.temperature": None})
    assert kwargs["reasoning_effort"] == "medium"
    assert "temperature" not in kwargs


def test_gpt_4o_rejects_a_luna_reasoning_setting(client: DefaultLlmClient) -> None:
    with pytest.raises(ConfigError, match="gpt-4o.*reasoning_effort"):
        client._call_kwargs("who?", {"llm.model": "gpt-4o", "llm.reasoning_effort": "medium"})


def test_gpt_6_luna_allows_temperature_only_with_no_reasoning(
    client: DefaultLlmClient,
) -> None:
    kwargs = client._call_kwargs("who?", {"llm.reasoning_effort": "none", "llm.temperature": 0.2})
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["temperature"] == 0.2


def test_an_empty_model_is_rejected_rather_than_defaulted(
    client: DefaultLlmClient,
) -> None:
    with pytest.raises(ConfigError, match="no model configured"):
        client._call_kwargs("who?", {"llm.model": ""})


def test_a_per_call_property_overrides_the_constructed_one() -> None:
    client = DefaultLlmClient({"llm.model": "constructed"})
    merged = {**client.properties, "llm.model": "per-call"}
    assert client._call_kwargs("who?", merged)["model"] == "per-call"


@pytest.mark.parametrize("asked", [True, False])
def test_the_stream_flag_reaches_the_provider_as_given(
    client: DefaultLlmClient, asked: bool
) -> None:
    """Both values are forwarded, because both are things a provider may need to be told.

    `True` was refused by name for a while, which left the callers whose provider
    configuration or proxy requires a stream unable to use the package at all.
    """
    assert client._call_kwargs("who?", {"llm.stream": asked})["stream"] is asked
