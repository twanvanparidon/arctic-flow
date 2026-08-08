"""Creating the home layer.

Real directories under `tmp_path`, and the config that lands there is fed back through the
real loader. What `init` writes has to be readable by the thing that reads it, and only
loading it says so: a scaffold with a typo in it passes every check that only counts files.
"""

from __future__ import annotations

from pathlib import Path

from commands.init import initialise
from paths.config import CONFIG_FILE, load
from paths.resolver import Paths


class TestInitialise:
    def test_it_writes_the_tree_and_the_config(self, paths: Paths, home: Path) -> None:
        result = initialise(paths)
        root = home / ".arctic"
        assert root.is_dir()
        for subdir in ("tools", "agents", "flows"):
            assert (root / subdir).is_dir()
        assert (root / CONFIG_FILE).is_file()
        assert result.path == root

    def test_it_reports_what_it_wrote(self, paths: Paths) -> None:
        created = initialise(paths).created
        assert set(created) == {"tools/", "agents/", "flows/", CONFIG_FILE}

    def test_the_directory_it_makes_becomes_a_search_root(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        """The whole point of the command. `~/.arctic` was always searched; `roots` drops
        one that is not there, so creating it is what turns the layer on."""
        assert home / ".arctic" not in paths.roots
        initialise(paths)
        assert home / ".arctic" in Paths(workspace, env={}, home=home).roots

    def test_the_config_it_writes_loads(self, paths: Paths, home: Path) -> None:
        """A shipped file with a typo in it would otherwise break every command, for
        everyone who ran `init`, at the next command rather than at this one."""
        initialise(paths)
        assert load(home / ".arctic").sources == ()


class TestRunningItAgain:
    def test_nothing_is_reported_as_created_twice(self, paths: Paths) -> None:
        initialise(paths)
        second = initialise(paths)
        assert second.created == ()
        assert set(second.existing) == {"tools/", "agents/", "flows/", CONFIG_FILE}

    def test_an_edited_config_survives(self, paths: Paths, home: Path) -> None:
        """The one thing someone fears from re-running `init`. `create` refuses an
        existing name; this cannot, because being run again is what it is for."""
        initialise(paths)
        (home / ".arctic" / CONFIG_FILE).write_text("sources:\n  - /shared\n")
        initialise(paths)
        assert load(home / ".arctic").sources == (Path("/shared"),)

    def test_only_what_was_missing_is_created(self, paths: Paths, home: Path) -> None:
        initialise(paths)
        (home / ".arctic" / "agents").rmdir()
        result = initialise(paths)
        assert result.created == ("agents/",)
        assert CONFIG_FILE in result.existing

    def test_a_component_already_there_is_untouched(self, paths: Paths, home: Path) -> None:
        initialise(paths)
        tool = home / ".arctic" / "tools" / "mine"
        tool.mkdir()
        (tool / "spec.json").write_text("{}")
        initialise(paths)
        assert (tool / "spec.json").read_text() == "{}"
