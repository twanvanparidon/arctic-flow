"""The docs sync, and the link rewriting it rests on.

A link left alone would point at a page the flat references directory does not hold, which
reaches a user as a skill citing something it cannot read.

The tree checks run against the real `docs/`: a fixture tree would only prove the walker
walks.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

# Loaded from its file rather than imported: packaging/ is not on the path, and its name is
# taken by the `packaging` distribution that setuptools and jsonschema pull in.
_spec = importlib.util.spec_from_file_location("sync_docs", _ROOT / "packaging" / "sync_docs.py")
assert _spec is not None and _spec.loader is not None
sync_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_docs)


class TestRewrite:
    def test_a_link_to_another_synced_page_is_left_alone(self) -> None:
        assert sync_docs._rewrite("components.md", "flows.md") == "components.md"

    def test_a_link_to_a_page_that_is_not_synced_becomes_an_absolute_url(self) -> None:
        """The skill has no copy to read, so the only useful answer is where to fetch it."""
        assert sync_docs._rewrite("design.md", "flows.md") == f"{sync_docs.BLOB}/docs/design.md"

    def test_a_link_out_of_docs_becomes_an_absolute_url(self) -> None:
        assert sync_docs._rewrite("../examples/README.md", "README.md") == (
            f"{sync_docs.BLOB}/examples/README.md"
        )

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("cli.md#vault", "cli.md#vault"),
            ("design.md#loops", f"{sync_docs.BLOB}/docs/design.md#loops"),
        ],
    )
    def test_an_anchor_survives_the_rewrite(self, target: str, expected: str) -> None:
        assert sync_docs._rewrite(target, "flows.md") == expected


class TestRender:
    def test_the_copy_names_the_page_it_came_from(self) -> None:
        """Someone editing a reference has to be told where the real file is."""
        header = sync_docs.render("reference.md").splitlines()[0]
        assert "docs/reference.md" in header
        assert "packaging/sync_docs.py" in header

    def test_no_link_survives_pointing_outside_the_references_directory(self) -> None:
        for source in sync_docs.SYNCED:
            for _, target in sync_docs.LINK.findall(sync_docs.render(source)):
                assert "/" not in target, f"{source} still carries a path: {target}"


class TestTheRealTree:
    def test_the_user_facing_pages_are_flat(self) -> None:
        """A new page is a new topic, and a section is a heading. `design/` is the exception."""
        assert sync_docs._check_layout() == []
        assert [p.name for p in sorted(sync_docs.DOCS.iterdir()) if p.is_dir()] == ["design"]

    def test_design_is_not_synced_into_the_skill(self) -> None:
        """A skill helping someone write a flow does not need the rationale."""
        assert not any(name.startswith("design/") for name in sync_docs.SYNCED)

    def test_every_synced_page_exists(self) -> None:
        for source in sync_docs.SYNCED:
            assert (sync_docs.DOCS / source).exists(), source

    def test_the_tree_passes_its_own_checks(self) -> None:
        """Links resolve, every page is reachable from the index, none is over the limit."""
        assert sync_docs._check_tree() == []

    def test_the_committed_references_are_in_sync(self) -> None:
        """The same claim CI makes, so a stale reference fails here first."""
        for source, destination in sync_docs.SYNCED.items():
            path = sync_docs.REFERENCES / destination
            assert path.exists(), destination
            assert path.read_text() == sync_docs.render(source), (
                f"{destination} is stale. Run python packaging/sync_docs.py"
            )
