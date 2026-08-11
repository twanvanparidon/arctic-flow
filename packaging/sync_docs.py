"""Copy the docs pages the Claude Code skill reads, and check the docs tree.

`docs/` is the single source of truth for user-facing prose. The `help` skill in
`.claude-plugins/` ships its references inside the plugin rather than fetching them, because
the marketplace installs from `main` and a skill that needs a network is a skill that fails
offline. That leaves two copies of the same rules, which is the arrangement that drifts.

So the copies are generated from `docs/` and committed, and CI runs this with `--check`. A
docs edit that was not synced fails the gate instead of shipping a reference that disagrees
with the engine.

    python packaging/sync_docs.py            # write the references
    python packaging/sync_docs.py --check    # fail if they are stale, or the tree is wrong

`--check` also enforces the rules the docs are written to, since it is already walking the
tree: every relative link resolves, every page is reachable from the index, and no page runs
past MAX_LINES. The length limit is the one that needs a machine: prose grows a line at a
time and nobody notices until a page nobody reads is 300 lines long.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REFERENCES = ROOT / ".claude-plugins" / "skills" / "help" / "references"

BLOB = "https://github.com/twanvanparidon/arctic-flow/blob/main"

# The pages the skill carries: everything a flow author needs. `design.md` is left out because
# a skill helping someone write a flow does not need the rationale, and `README.md` because the
# skill's own body is its index. Both are still reachable by link, as a URL.
SYNCED: dict[str, str] = {
    "setup.md": "setup.md",
    "projects.md": "projects.md",
    "flows.md": "flows.md",
    "components.md": "components.md",
    "cli.md": "cli.md",
    "reference.md": "reference.md",
}

# One document per topic, so a page is naturally longer than a section was. The limit is here
# to catch drift, not to shape a page: prose grows a line at a time and nobody notices.
MAX_LINES = 280

LENGTH_EXEMPT: set[str] = set()

INDEX = "README.md"

# The user-facing pages are flat: a new one means a new topic, and a section is a heading.
# `design/` is the one directory, because it is written for a different reader and is allowed
# to go deeper than a user page ever should.
DESIGN = "design"

# Matches an inline markdown link with a relative target. Anything with a scheme, an anchor
# alone, or a mailto: is left as it is.
LINK = re.compile(r"\[([^\]]*)\]\((?!https?://|#|mailto:)([^)]+)\)")


def _generated_header(source: str) -> str:
    return f"<!-- Generated from docs/{source} by packaging/sync_docs.py. Edit that file. -->\n\n"


def _rewrite(target: str, source: str) -> str:
    """Point a docs-relative link somewhere the flat references directory can follow."""
    anchor = ""
    if "#" in target:
        target, anchor = target.split("#", 1)
        anchor = "#" + anchor

    resolved = ((DOCS / source).parent / target).resolve()
    try:
        relative = resolved.relative_to(DOCS).as_posix()
    except ValueError:
        # Out of docs/ entirely: examples/, CONTRIBUTING.md, the repository itself.
        return f"{BLOB}/{resolved.relative_to(ROOT).as_posix()}{anchor}"

    if relative in SYNCED:
        return f"{SYNCED[relative]}{anchor}"
    return f"{BLOB}/docs/{relative}{anchor}"


def render(source: str) -> str:
    text = (DOCS / source).read_text()
    body = LINK.sub(lambda m: f"[{m.group(1)}]({_rewrite(m.group(2), source)})", text)
    return _generated_header(source) + body


def _pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md"))


def _check_layout() -> list[str]:
    """Flat user-facing pages, with `design/` the one directory allowed under docs/."""
    return [
        f"docs/{path.name}/: docs/ is flat apart from docs/{DESIGN}/"
        for path in sorted(DOCS.iterdir())
        if path.is_dir() and path.name != DESIGN
    ]


def _check_tree() -> list[str]:
    problems: list[str] = _check_layout()

    for page in _pages():
        relative = page.relative_to(DOCS).as_posix()
        lines = len(page.read_text().splitlines())
        if lines > MAX_LINES and relative not in LENGTH_EXEMPT:
            problems.append(f"docs/{relative}: {lines} lines, over the {MAX_LINES} limit")

        for _, target in LINK.findall(page.read_text()):
            path = target.split("#", 1)[0]
            if not path:
                continue
            if not (page.parent / path).resolve().exists():
                problems.append(f"docs/{relative}: link to '{target}' does not resolve")

    problems.extend(_check_reachable())
    return problems


def _check_reachable() -> list[str]:
    """Every page has to be reachable from the index, or it is a page nobody can find."""
    seen: set[str] = set()
    queue = [INDEX]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        page = DOCS / current
        if not page.exists():
            continue
        for _, target in LINK.findall(page.read_text()):
            path = target.split("#", 1)[0]
            if not path or not path.endswith(".md"):
                continue
            resolved = (page.parent / path).resolve()
            if resolved.is_relative_to(DOCS):
                queue.append(resolved.relative_to(DOCS).as_posix())

    orphans = {p.relative_to(DOCS).as_posix() for p in _pages()} - seen
    return [f"docs/{name}: not reachable from docs/{INDEX}" for name in sorted(orphans)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report problems instead of writing; non-zero if there are any",
    )
    args = parser.parse_args()

    missing = [name for name in SYNCED if not (DOCS / name).exists()]
    if missing:
        for name in missing:
            print(f"docs/{name}: named in SYNCED but not present", file=sys.stderr)
        return 1

    problems = _check_tree() if args.check else []

    for source, destination in SYNCED.items():
        wanted = render(source)
        path = REFERENCES / destination
        if args.check:
            current = path.read_text() if path.exists() else None
            if current != wanted:
                problems.append(
                    f"{path.relative_to(ROOT)}: stale. Run python packaging/sync_docs.py"
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(wanted)

    if args.check:
        stale = {REFERENCES / name for name in SYNCED.values()}
        for path in sorted(REFERENCES.glob("*.md")):
            if path not in stale:
                problems.append(f"{path.relative_to(ROOT)}: not generated from docs/")

        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"\n{len(problems)} problem(s)", file=sys.stderr)
            return 1
        print(f"docs ok: {len(_pages())} pages, {len(SYNCED)} references in sync")
        return 0

    print(f"wrote {len(SYNCED)} references to {REFERENCES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
