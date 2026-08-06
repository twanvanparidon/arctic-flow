"""Turn pytest's JUnit XML into the markdown GitHub puts at the top of a run.

    python packaging/junit_summary.py reports/*.xml >> "$GITHUB_STEP_SUMMARY"

The counts are in the log too, but only if you open it and scroll. This puts them on the run
page, one row per suite, so a pull request answers "how many, and which broke" without being
clicked into.

Written rather than pulled in as an action: a third-party action would be one more thing to
pin and to trust, for something the standard library does in forty lines.

Only failures and skips are listed by name. A list of the tests that passed is hundreds of
lines saying nothing, and the count already covers it. Skips are named because this suite
skips for a reason worth reading: a missing `jq` means the shipped examples went unchecked.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Suite:
    """One XML file's worth of results."""

    name: str
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0
    problems: list[tuple[str, str]] = field(default_factory=list)
    skips: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped

    @property
    def mark(self) -> str:
        return "❌" if self.failures or self.errors else "✅"


def identify(case: ElementTree.Element) -> str:
    """`module.Class.test_name`, as pytest would report it."""
    return f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")


def first_line(element: ElementTree.Element) -> str:
    """The message attribute, or the first line of the body if there is no message."""
    message = element.get("message") or (element.text or "").strip()
    return message.splitlines()[0][:200] if message else "no detail given"


def read(path: Path) -> Suite:
    root = ElementTree.parse(path).getroot()
    # pytest wraps its one testsuite in a testsuites element; other writers do not.
    element = root if root.tag == "testsuite" else root[0]
    suite = Suite(
        name=path.stem,
        tests=int(element.get("tests", 0)),
        failures=int(element.get("failures", 0)),
        errors=int(element.get("errors", 0)),
        skipped=int(element.get("skipped", 0)),
        seconds=float(element.get("time", 0)),
    )
    for case in element.iter("testcase"):
        for outcome in case:
            if outcome.tag in ("failure", "error"):
                suite.problems.append((identify(case), first_line(outcome)))
            elif outcome.tag == "skipped":
                suite.skips.append((identify(case), first_line(outcome)))
    return suite


def render(suites: list[Suite]) -> str:
    lines = [
        "## Tests",
        "",
        "| | suite | tests | passed | failed | skipped | time |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for suite in suites:
        lines.append(
            f"| {suite.mark} | {suite.name} | {suite.tests} | {suite.passed} | "
            f"{suite.failures + suite.errors} | {suite.skipped} | {suite.seconds:.1f}s |"
        )

    problems = [entry for suite in suites for entry in suite.problems]
    if problems:
        lines += ["", "### Failed", ""]
        lines += [f"- `{name}`  {detail}" for name, detail in problems]

    skips = [entry for suite in suites for entry in suite.skips]
    if skips:
        lines += ["", "### Skipped", ""]
        lines += [f"- `{name}`  {detail}" for name, detail in skips]

    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Reports are summarised in the order given, and a missing one is not fatal.

    A suite that died before writing its XML has already failed the build. Refusing to
    summarise the other suite as well would take away the numbers at the moment they are
    most wanted.
    """
    paths = [Path(argument) for argument in argv]
    present = [path for path in paths if path.is_file()]
    for absent in [path for path in paths if not path.is_file()]:
        print(f"no report at {absent}, leaving it out of the summary", file=sys.stderr)
    if not present:
        print(f"usage: {Path(__file__).name} REPORT.xml [REPORT.xml ...]", file=sys.stderr)
        return 2
    print(render([read(path) for path in present]), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
