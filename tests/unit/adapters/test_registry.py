"""The adapter registry, and the contract every entry in it keeps.

`ADAPTERS` is static imports on purpose: a frozen build misses anything resolved by name.
So the interesting test is not that lookup works, it is that every module in the dict really
does declare what the engine reads off it. That check runs over the registry rather than
over a list written here, so a second adapter is held to it the day it is added.
"""

from __future__ import annotations

from types import ModuleType

import pytest
from jsonschema import Draft202012Validator

import adapters
from adapters import (
    ADAPTERS,
    AdapterError,
    AdapterProtocolError,
    AdapterRunFailed,
    AdapterUnavailable,
)


class TestLookup:
    def test_finds_a_registered_adapter(self) -> None:
        assert adapters.get("claude_code") is ADAPTERS["claude_code"]

    def test_an_unregistered_name_lists_what_there_is(self) -> None:
        """There is no `~/.arctic/adapters/`, so "install it" is not the advice to give."""
        with pytest.raises(AdapterError, match="unknown adapter 'gpt'. Available: claude_code"):
            adapters.get("gpt")

    def test_the_lookup_error_does_not_chain_a_key_error(self) -> None:
        with pytest.raises(AdapterError) as caught:
            adapters.get("gpt")
        assert caught.value.__cause__ is None

    def test_names_are_sorted(self) -> None:
        assert adapters.names() == sorted(ADAPTERS)

    def test_describe_pairs_each_name_with_its_one_line_description(self) -> None:
        assert adapters.describe()["claude_code"] == ADAPTERS["claude_code"].DESCRIPTION


class TestTheContractEveryAdapterKeeps:
    @pytest.mark.parametrize("adapter", ADAPTERS.values(), ids=list(ADAPTERS))
    def test_it_declares_a_name_that_matches_its_registration(self, adapter: ModuleType) -> None:
        assert ADAPTERS[adapter.NAME] is adapter

    @pytest.mark.parametrize("adapter", ADAPTERS.values(), ids=list(ADAPTERS))
    def test_it_describes_itself_in_one_line(self, adapter: ModuleType) -> None:
        assert adapter.DESCRIPTION.strip()
        assert "\n" not in adapter.DESCRIPTION

    @pytest.mark.parametrize("adapter", ADAPTERS.values(), ids=list(ADAPTERS))
    def test_its_input_schema_is_a_valid_schema(self, adapter: ModuleType) -> None:
        """The engine validates every payload against it, so a typo here fails every turn."""
        assert Draft202012Validator.check_schema(adapter.INPUT_SCHEMA) is None

    @pytest.mark.parametrize("adapter", ADAPTERS.values(), ids=list(ADAPTERS))
    def test_it_requires_a_prompt_and_nothing_the_engine_does_not_send(
        self, adapter: ModuleType
    ) -> None:
        assert adapter.INPUT_SCHEMA["required"] == ["prompt"]

    @pytest.mark.parametrize("adapter", ADAPTERS.values(), ids=list(ADAPTERS))
    def test_it_can_be_run(self, adapter: ModuleType) -> None:
        assert callable(adapter.run)


class TestFailureKinds:
    @pytest.mark.parametrize("kind", [AdapterUnavailable, AdapterRunFailed, AdapterProtocolError])
    def test_every_kind_is_an_adapter_error(self, kind: type[Exception]) -> None:
        """A caller that only cares that the turn failed catches the base class."""
        assert issubclass(kind, AdapterError)

    def test_an_adapter_error_is_a_runtime_error(self) -> None:
        """`commands.EXPECTED_ERRORS` catches FlowError, which is where these are re-raised."""
        assert issubclass(AdapterError, RuntimeError)

    def test_the_kinds_are_distinct(self) -> None:
        """A missing runtime is a host problem; a refused turn is not. Retrying differs."""
        assert not issubclass(AdapterUnavailable, AdapterRunFailed)
        assert not issubclass(AdapterRunFailed, AdapterProtocolError)
