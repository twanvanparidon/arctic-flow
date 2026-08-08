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
from engine import specs


class TestLookup:
    def test_finds_a_registered_adapter(self) -> None:
        assert adapters.get("claude_code") is ADAPTERS["claude_code"]

    def test_an_unregistered_name_lists_what_there_is(self) -> None:
        """There is no `~/.arctic/adapters/`, so "install it" is not the advice to give."""
        with pytest.raises(AdapterError, match=r"unknown adapter 'gpt'.*claude_code"):
            adapters.get("gpt")

    def test_the_lookup_error_does_not_chain_a_key_error(self) -> None:
        with pytest.raises(AdapterError) as caught:
            adapters.get("gpt")
        assert caught.value.__cause__ is None

    def test_locate_finds_the_module_an_adapter_is(self) -> None:
        """`atf list` reports where every component came from, and an adapter comes
        from a module rather than from a search root."""
        found = adapters.locate(ADAPTERS["claude_code"])
        assert found.name == "claude_code.py"
        assert found.is_file()


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
        """An adapter may require more than the prompt, but only things the engine sends.
        Requiring anything else would refuse every spec and be found at the first turn."""
        sendable = {"prompt", "system", "json_schema", *specs.FORWARDED.values()}
        assert "prompt" in adapter.INPUT_SCHEMA["required"]
        assert set(adapter.INPUT_SCHEMA["required"]) <= sendable


class TestFailureKinds:
    @pytest.mark.parametrize("kind", [AdapterUnavailable, AdapterRunFailed, AdapterProtocolError])
    def test_every_kind_is_an_adapter_error(self, kind: type[Exception]) -> None:
        """A caller that only cares that the turn failed catches the base class."""
        assert issubclass(kind, AdapterError)

    def test_an_adapter_error_is_a_runtime_error(self) -> None:
        """`commands.EXPECTED_ERRORS` catches FlowError, which is where these are re-raised."""
        assert issubclass(AdapterError, RuntimeError)
