"""The stamper, and the coupling it rests on.

Its regex has to match the assignment in the real src/cli/branding.py. Nothing else checks
that, and a stamp that silently matched nothing ships the placeholder under a release tag.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

# Loaded from its file rather than imported: packaging/ is not on the path, and its name is
# taken by the `packaging` distribution that setuptools and jsonschema pull in.
_spec = importlib.util.spec_from_file_location(
    "stamp_version", _ROOT / "packaging" / "stamp_version.py"
)
assert _spec is not None and _spec.loader is not None
stamp_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_version)


class TestStamp:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("v0.2.0", "0.2.0"),
            ("v0.2.0-rc.1", "0.2.0-rc.1"),
            ("v10.20.30-rc.99", "10.20.30-rc.99"),
        ],
    )
    def test_the_tag_becomes_the_version_without_its_v(self, tag: str, expected: str) -> None:
        assert stamp_version.stamp('__version__ = "0.0.0.dev0"\n', tag) == (
            f'__version__ = "{expected}"\n'
        )

    @pytest.mark.parametrize(
        "tag",
        ["0.2.0", "v0.2", "v0.2.0-beta.1", "v0.2.0-rc1", "v0.2.0-rc.", "vX.Y.Z", ""],
    )
    def test_a_tag_no_pipeline_would_build_is_refused(self, tag: str) -> None:
        """The shapes here are the tag filter's. A version the workflow would not start a
        run for must not reach an artefact that claims to be that release."""
        with pytest.raises(ValueError, match="not a release tag"):
            stamp_version.stamp('__version__ = "0.0.0.dev0"\n', tag)

    def test_the_surrounding_source_is_left_alone(self) -> None:
        source = 'NAME = "Arctic Flow"\n__version__ = "0.0.0.dev0"\nCOMMAND = "atf"\n'
        assert stamp_version.stamp(source, "v1.2.3") == (
            'NAME = "Arctic Flow"\n__version__ = "1.2.3"\nCOMMAND = "atf"\n'
        )

    @pytest.mark.parametrize(
        "source",
        [
            "",
            '__version__: str = "0.0.0.dev0"\n',
            "__version__ = '0.0.0.dev0'\n",
            '__version__ = "0.1.0"\n__version__ = "0.2.0"\n',
        ],
    )
    def test_it_refuses_a_source_it_did_not_rewrite_exactly_once(self, source: str) -> None:
        """Silence is the dangerous outcome: a stamp that matched nothing builds a binary
        reporting the placeholder, and only release.sh notices, after the artefact exists."""
        with pytest.raises(ValueError, match="one __version__ assignment"):
            stamp_version.stamp(source, "v1.2.3")


class TestBrandingIsStampable:
    def test_the_real_branding_module_carries_one_matching_assignment(self) -> None:
        """The coupling that has no other guard: reformat that line and every release after
        it ships the placeholder."""
        source = (_ROOT / "src" / "cli" / "branding.py").read_text()
        assert '__version__ = "1.2.3"' in stamp_version.stamp(source, "v1.2.3")

    def test_the_placeholder_is_not_mistakable_for_a_release(self) -> None:
        """An unstamped build has to be obvious in `atf --version`. A plausible number there
        is the one thing that would let a missed stamp reach a user unnoticed."""
        from cli import branding

        assert "dev" in branding.__version__
