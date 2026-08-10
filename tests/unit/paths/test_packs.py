"""Packs: the shipped components that resolve only once `config.yaml` names them.

Real files and the real built-in root throughout. The pack under test is the `git` pack
that ships, because the whole claim being made is about what happens to a name that is
*there*: a stand-in pack written into `tmp_path` would be a source with a different
spelling, and could not fail the way this can.

What each test here protects is a different half of "opt in". A pack that is off must be
absent from the lookup, or the switch does nothing. A pack that is off must fail *saying
so*, or the switch is a trap. And a name it defines must still be the engine's, because
that is the whole reason a pack is not a source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paths.config import CONFIG_FILE, ConfigError
from paths.resolver import (
    ENGINE_NAMESPACE,
    LookupError_,
    Paths,
    available_packs,
    builtin_root,
    packs_root,
)

# One tool out of the pack that ships. Named once here so a rename is one edit.
A_PACK_TOOL = "arctic/git/log"


def configure(home: Path, text: str) -> Path:
    (home / ".arctic").mkdir(parents=True, exist_ok=True)
    (home / ".arctic" / CONFIG_FILE).write_text(text)
    return home


def resolver(workspace: Path, home: Path) -> Paths:
    return Paths(workspace, env={}, home=home)


class TestAvailablePacks:
    def test_the_git_pack_ships(self) -> None:
        assert "git" in available_packs()

    def test_a_pack_sits_inside_the_built_in_root(self) -> None:
        """Which is the whole mechanism: `arctic/` resolves inside `builtin_root()` or
        nowhere, so a pack under it needs no exception to define one."""
        assert builtin_root() in available_packs()["git"].path.parents

    def test_it_carries_what_the_manifest_said(self) -> None:
        pack = available_packs()["git"]
        assert pack.description
        assert "git" in pack.requires

    def test_the_name_is_the_directory(self) -> None:
        """A name that could disagree with the directory it is written in is a name with
        two answers, and `config.yaml` has to spell the directory."""
        assert available_packs()["git"].path == packs_root() / "git"


class TestSwitchedOff:
    def test_a_pack_tool_does_not_resolve(self, workspace: Path, home: Path) -> None:
        with pytest.raises(LookupError_):
            resolver(workspace, home).find("tool", A_PACK_TOOL)

    def test_the_failure_names_the_pack(self, workspace: Path, home: Path) -> None:
        """The reason this is not the ordinary "unknown tool": every root really was
        searched and the tool really is in none of them. What is wrong is a config file."""
        with pytest.raises(LookupError_, match="'git' pack"):
            resolver(workspace, home).find("tool", A_PACK_TOOL)

    def test_the_failure_names_the_line_to_add(self, workspace: Path, home: Path) -> None:
        with pytest.raises(LookupError_, match=r"packs: \[git\]"):
            resolver(workspace, home).find("tool", A_PACK_TOOL)

    def test_it_is_absent_from_the_listing(self, workspace: Path, home: Path) -> None:
        assert A_PACK_TOOL not in resolver(workspace, home).list("tool")

    def test_a_name_no_pack_defines_still_fails_the_ordinary_way(
        self, workspace: Path, home: Path
    ) -> None:
        """The pack scan must not swallow every miss. A tool nobody defines is still
        reported as one, with the roots that were searched."""
        with pytest.raises(LookupError_, match="looked in"):
            resolver(workspace, home).find("tool", "ghost")


class TestSwitchedOn:
    @pytest.fixture
    def enabled(self, workspace: Path, home: Path) -> Paths:
        configure(home, "packs:\n  - git\n")
        return resolver(workspace, home)

    def test_a_pack_tool_resolves(self, enabled: Paths) -> None:
        assert (enabled.find("tool", A_PACK_TOOL) / "spec.json").is_file()

    def test_it_resolves_out_of_the_pack_directory(self, enabled: Paths) -> None:
        assert packs_root() / "git" in enabled.find("tool", A_PACK_TOOL).parents

    def test_it_is_listed_under_its_full_name(self, enabled: Paths) -> None:
        """Qualified, the way a flow spells it. A listing showing the leaf would name
        something no flow can ask for."""
        assert A_PACK_TOOL in enabled.list("tool")

    def test_the_pack_root_is_in_the_search_order(self, enabled: Paths) -> None:
        assert packs_root() / "git" in enabled.roots

    def test_it_sits_below_a_source(self, workspace: Path, home: Path, tmp_path: Path) -> None:
        """A pack is the engine's own, so nothing it holds competes with a source. The
        order still has to hold, because a later pack may not ship only `arctic/` names."""
        shared = tmp_path / "shared"
        shared.mkdir()
        configure(home, f"sources:\n  - {shared}\npacks:\n  - git\n")
        roots = resolver(workspace, home).roots
        assert roots.index(shared) < roots.index(packs_root() / "git")

    def test_the_display_names_it_as_the_engine_s(self, enabled: Paths) -> None:
        found = enabled.find("tool", A_PACK_TOOL)
        assert enabled.display(found).startswith("$ATF_ROOT/packs/git")


class TestTheNamespaceStillBelongsToTheEngine:
    """A pack defines names under `arctic/`, and that is exactly what a source may not do.

    The reservation is what makes the name worth reading: `tool: arctic/git/commit` has to
    mean the tool that shipped under it. Enabling a pack must not weaken that, and these
    are the two halves of not weakening it.
    """

    def test_a_workspace_copy_of_a_pack_name_is_refused(self, workspace: Path, home: Path) -> None:
        configure(home, "packs:\n  - git\n")
        intruder = workspace / "tools" / A_PACK_TOOL
        intruder.mkdir(parents=True)
        (intruder / "spec.json").write_text("{}")

        with pytest.raises(LookupError_, match=f"belongs to the {'engine'}"):
            resolver(workspace, home).find("tool", A_PACK_TOOL)

    def test_a_workspace_copy_is_refused_even_with_the_pack_off(
        self, workspace: Path, home: Path
    ) -> None:
        """Off is not a hole in the reservation. Otherwise the way to define
        `arctic/git/log` would be to leave the pack switched off, which is the whole
        property gone."""
        intruder = workspace / "tools" / A_PACK_TOOL
        intruder.mkdir(parents=True)
        (intruder / "spec.json").write_text("{}")

        with pytest.raises(LookupError_, match=ENGINE_NAMESPACE):
            resolver(workspace, home).find("tool", A_PACK_TOOL)


class TestAnUnknownPack:
    def test_it_is_refused_rather_than_ignored(self, workspace: Path, home: Path) -> None:
        """A misspelled name would otherwise build a root that does not exist, which is
        dropped, so the whole of what a typo does is make every tool quietly missing."""
        configure(home, "packs:\n  - gti\n")
        with pytest.raises(ConfigError, match="gti"):
            resolver(workspace, home)

    def test_the_refusal_names_the_packs_there_are(self, workspace: Path, home: Path) -> None:
        configure(home, "packs:\n  - gti\n")
        with pytest.raises(ConfigError, match="git"):
            resolver(workspace, home)

    def test_it_stops_every_command_rather_than_one(self, workspace: Path, home: Path) -> None:
        """Raised from the constructor, like a config that will not parse: a `Paths` that
        cannot be built is a command that cannot start."""
        configure(home, "packs:\n  - gti\n")
        with pytest.raises(ConfigError):
            resolver(workspace, home).list("tool")

    def test_a_name_that_is_not_a_string_is_refused_by_the_schema(
        self, workspace: Path, home: Path
    ) -> None:
        configure(home, "packs:\n  - 3\n")
        with pytest.raises(ConfigError, match="packs"):
            resolver(workspace, home)
