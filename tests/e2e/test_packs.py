"""Packs, in the artefact.

One question, and it is the only one about packs that this suite can ask: does the bundle
actually carry `builtin/packs/`?

A pack is package data three layers deep. `[tool.setuptools.package-data]` has to sweep it
into the wheel, `collect_data_files("builtin")` has to sweep it out of the wheel into the
bundle, and `run.sh` has to arrive with its executable bit and find the helper it sources
four directories up. Every one of those holds against `src/`, where the files are simply
sitting on disk, and the failure is a shipped binary whose `arctic/git/log` cannot be found
or cannot be run. That is the definition of a test that belongs here.

The pack is switched on through a `$HOME` this suite writes, because that is the only way
in: `packs:` is read from `~/.arctic/config.yaml` and there is deliberately no flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import repository
from support.outcome import Runner

from .conftest import requires

A_PACK_TOOL = "arctic/git/log"


# Every pack that ships. Named here rather than read off the source tree, because the
# subject is the artefact: a pack that exists in `src/` and not in the bundle is precisely
# the failure this file is for, and a list derived from `src/` could not see it.
PACKS = ("bitbucket", "git", "github")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home directory with every shipped pack switched on."""
    root = tmp_path / "home"
    (root / ".arctic").mkdir(parents=True)
    enabled = "".join(f"  - {pack}\n" for pack in PACKS)
    (root / ".arctic" / "config.yaml").write_text(f"packs:\n{enabled}")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository for the binary to act on.

    The same helper the integration suite uses, so what differs between the two files is
    the thing being driven and nothing else.
    """
    root = repository.initialise(tmp_path / "repo")
    repository.commit(root, "first commit", **{"a.txt": "one\n"})
    return root


class TestTheBundleCarriesThePacks:
    @pytest.mark.parametrize("pack", PACKS)
    def test_every_pack_is_listed(self, atf: Runner, home: Path, pack: str) -> None:
        """A bundle that dropped `packs/` lists none, which is what a package-data entry
        that did not cover a new directory produces. Per pack rather than in one go, so a
        failure names the directory that did not ship."""
        assert pack in atf("list", env={"HOME": str(home)}).out

    def test_enabling_every_pack_at_once_is_accepted(self, atf: Runner, home: Path) -> None:
        """The config that `home` wrote names all of them, so a pack the binary does not
        carry is refused by `_check_packs` and this command exits non-zero."""
        assert atf("list", env={"HOME": str(home)}).code == 0

    def test_a_pack_tool_resolves(self, atf: Runner, home: Path) -> None:
        assert A_PACK_TOOL in atf("list", env={"HOME": str(home)}).out

    def test_its_spec_can_be_read_out_of_the_bundle(self, atf: Runner, home: Path) -> None:
        result = atf("inspect", "tool", A_PACK_TOOL, env={"HOME": str(home)})
        assert result.code == 0
        assert "max_commits" in result.out

    def test_it_is_reported_as_the_engine_s_own(self, atf: Runner, home: Path) -> None:
        """`$ATF_ROOT/packs/...`, not an absolute path into a PyInstaller directory."""
        listing = atf("list", env={"HOME": str(home)}).out
        line = next(one for one in listing.splitlines() if A_PACK_TOOL in one)
        assert "$ATF_ROOT/packs/git" in line


class TestAPackToolRunsOutOfTheBundle:
    def test_it_produces_the_answer(self, atf: Runner, home: Path, repo: Path) -> None:
        """The whole chain: the script kept its executable bit, found `lib/git.sh` four
        directories up, and spawned a system `git` from a frozen process."""
        requires("git", "jq")
        (repo / "flows").mkdir()
        (repo / "flows" / "history.yaml").write_text(
            "flow: history\n"
            "start: look\n"
            "steps:\n"
            "  - id: look\n"
            f"    tool: {A_PACK_TOOL}\n"
            "output:\n"
            "  template: '{{ steps.look.text }}'\n"
        )
        result = atf("--workspace", str(repo), "run", "history", env={"HOME": str(home)})
        assert result.code == 0, result.err
        assert "first commit" in result.out


class TestSwitchedOff:
    def test_a_pack_tool_does_not_resolve_without_the_config(
        self, atf: Runner, tmp_path: Path
    ) -> None:
        """The default install. Nothing about being frozen may turn the switch on."""
        bare = tmp_path / "empty-home"
        bare.mkdir()
        assert A_PACK_TOOL not in atf("list", env={"HOME": str(bare)}).out
