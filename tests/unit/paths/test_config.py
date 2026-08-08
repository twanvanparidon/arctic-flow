"""Reading `~/.arctic/config.yaml`.

Real files in `tmp_path`, because what is being checked is what happens to a file someone
typed: a key spelled wrong, a root written relative, a document that is not a mapping. A
stand-in for the filesystem would answer every one of those the same way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paths.config import CONFIG_FILE, Config, ConfigError, load


def write(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CONFIG_FILE).write_text(text)
    return directory


class TestLoad:
    def test_no_file_at_all_is_the_defaults(self, tmp_path: Path) -> None:
        """The ordinary case: nobody has run `init`, and nothing should refuse to start."""
        assert load(tmp_path / ".arctic") == Config()

    def test_an_empty_file_is_also_the_defaults(self, tmp_path: Path) -> None:
        assert load(write(tmp_path / ".arctic", "")) == Config()

    def test_sources_expand_a_tilde(self, tmp_path: Path) -> None:
        config = load(write(tmp_path / ".arctic", "sources:\n  - ~/shared\n"))
        assert config.sources == (Path.home() / "shared",)

    def test_sources_keep_the_order_they_were_written_in(self, tmp_path: Path) -> None:
        """They are search roots, so the order is the precedence between them."""
        config = load(write(tmp_path / ".arctic", "sources:\n  - /b\n  - /a\n"))
        assert config.sources == (Path("/b"), Path("/a"))


class TestWhatItRefuses:
    def test_an_unknown_key(self, tmp_path: Path) -> None:
        """The reason the schema is here at all: a mistyped setting that silently does
        nothing is worse than one that stops the command."""
        with pytest.raises(ConfigError, match="source"):
            load(write(tmp_path / ".arctic", "source:\n  - /shared\n"))

    def test_an_unknown_top_level_key(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="vault"):
            load(write(tmp_path / ".arctic", "vault: ~/secrets.vault\n"))

    def test_a_relative_source(self, tmp_path: Path) -> None:
        """It would resolve against wherever `atf` was run from, so the same config would
        mean a different thing in every directory."""
        with pytest.raises(ConfigError, match="components"):
            load(write(tmp_path / ".arctic", "sources:\n  - ./components\n"))

    def test_a_document_that_is_not_a_mapping(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="mapping"):
            load(write(tmp_path / ".arctic", "- one\n- two\n"))

    def test_something_that_is_not_yaml(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="valid YAML"):
            load(write(tmp_path / ".arctic", "run: [unclosed\n"))

    def test_every_problem_at_once_rather_than_the_first(self, tmp_path: Path) -> None:
        """The file is edited once and read again on the next command, so reporting one
        mistake at a time turns one fix into three."""
        with pytest.raises(ConfigError) as caught:
            load(write(tmp_path / ".arctic", "sources: nope\nextra: 1\n"))
        assert "sources" in str(caught.value) and "extra" in str(caught.value)

    def test_it_names_the_file(self, tmp_path: Path) -> None:
        """There are five search roots and only one config, so saying which file is the
        difference between fixing it and looking for it."""
        with pytest.raises(ConfigError, match=CONFIG_FILE):
            load(write(tmp_path / ".arctic", "nonsense: 1\n"))
