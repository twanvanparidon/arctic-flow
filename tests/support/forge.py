"""API doubles for the github and bitbucket packs.

A fake in the sense TESTING.md uses, sitting on top of `webserver`: it really routes by
method and path, really checks nothing it should not, and really answers JSON. A tool that
built the wrong path, sent the wrong verb, or forgot the Authorization header fails
against it. A stub answering whatever it was handed could not fail for any of the three.

Both forges are described by one dataclass, because the point of these two packs is that
they answer in the same shape. A test parametrised over `FORGES` runs the same flow
against both and compares against the same expectation, which is the only way to check
that claim rather than assert it in a doc.

`ROUTES` are keyed exactly as the tools request them, query string and all. That is
deliberate rather than lax matching: a request this file does not recognise 404s, so a
tool that quietly changed its pagination or dropped a filter fails here rather than
passing against a double that was happy with anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from support import webserver

BRANCH = "work"
REPO = "acme/widget"
TOKEN = "s3cret-token"

# What both packs must answer with, whatever the forge said. Only `mergeable` differs,
# and that difference is itself part of the contract: bitbucket cannot know.
EXPECTED = {
    "repo": REPO,
    "number": 42,
    "state": "open",
    "title": "Add the git pack",
    "source": BRANCH,
    "target": "main",
    "draft": False,
    "reviews": {"approved": 1, "changes_requested": 1},
    "checks": {"success": 1, "failure": 2, "pending": 1, "failing": ["test", "e2e"]},
}


@dataclass(frozen=True)
class Forge:
    """One forge, as everything a test needs to drive its pack against a double."""

    name: str
    token_env: str
    api_env: str
    remote: str
    routes: dict[str, Any]
    mergeable: bool | None
    # The path the API root carries under the host: none for github.com, `/2.0` for
    # bitbucket. Kept here so a test points the pack at the right place, and so the double
    # is asked for the same paths a real forge would be. It also means the suite covers an
    # API root with a path prefix at all, which is what GitHub Enterprise's /api/v3 is.
    prefix: str = ""
    expected: dict[str, Any] = field(default_factory=lambda: dict(EXPECTED))

    def tool(self, which: str) -> str:
        return f"arctic/{self.name}/pr/{which}"


# --- github -------------------------------------------------------------------

GITHUB_PULL = {
    "number": 42,
    "state": "open",
    "merged": False,
    "title": "Add the git pack",
    "head": {"ref": BRANCH, "sha": "abc1234"},
    "base": {"ref": "main"},
    "user": {"login": "twan"},
    "html_url": "https://github.com/acme/widget/pull/42",
    "draft": False,
    "mergeable": True,
}

# alice asked for a change and then approved, so she counts once and as an approval.
# bob only commented, which is a remark rather than a verdict.
GITHUB_REVIEWS = [
    {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
    {"state": "APPROVED", "user": {"login": "alice"}},
    {"state": "COMMENTED", "user": {"login": "bob"}},
    {"state": "CHANGES_REQUESTED", "user": {"login": "carol"}},
]

# One of each shape the mapping has to collapse: finished and fine, finished and not,
# finished but skipped (which is not a check saying no), and not finished at all.
GITHUB_CHECKS = {
    "check_runs": [
        {"name": "build", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "completed", "conclusion": "failure"},
        {"name": "e2e", "status": "completed", "conclusion": "timed_out"},
        {"name": "deploy", "status": "in_progress", "conclusion": None},
    ]
}

GITHUB_ROUTES: dict[str, Any] = {
    f"GET /repos/{REPO}": webserver.json_ok({"default_branch": "main"}),
    f"GET /repos/{REPO}/pulls/42": webserver.json_ok(GITHUB_PULL),
    f"GET /repos/{REPO}/pulls/42/reviews?per_page=100": webserver.json_ok(GITHUB_REVIEWS),
    f"GET /repos/{REPO}/commits/abc1234/check-runs?per_page=100": webserver.json_ok(GITHUB_CHECKS),
    f"GET /repos/{REPO}/pulls?state=open&head=acme:{BRANCH}&per_page=100": webserver.json_ok(
        [GITHUB_PULL]
    ),
    f"POST /repos/{REPO}/pulls": webserver.json_ok(GITHUB_PULL, code=201),
    f"POST /repos/{REPO}/issues/42/comments": webserver.json_ok(
        {
            "id": 999,
            "html_url": "https://github.com/acme/widget/pull/42#issuecomment-999",
            "user": {"login": "atf-bot"},
        },
        code=201,
    ),
    f"GET /repos/{REPO}/pulls/77": webserver.status(404, b'{"message":"Not Found"}'),
}

GITHUB = Forge(
    name="github",
    token_env="GITHUB_TOKEN",
    api_env="GITHUB_API_URL",
    remote=f"git@github.com:{REPO}.git",
    routes=GITHUB_ROUTES,
    mergeable=True,
)

# --- bitbucket ----------------------------------------------------------------

BITBUCKET_PULL = {
    "id": 42,
    "state": "OPEN",
    "title": "Add the git pack",
    "source": {"branch": {"name": BRANCH}},
    "destination": {"branch": {"name": "main"}},
    "author": {"nickname": "twan", "display_name": "Twan van Paridon"},
    "links": {"html": {"href": "https://bitbucket.org/acme/widget/pull-requests/42"}},
    "draft": False,
    # Bitbucket keeps one participant per person, so there is no superseding to undo.
    "participants": [
        {"approved": True, "state": "approved", "user": {"nickname": "alice"}},
        {"approved": False, "state": "changes_requested", "user": {"nickname": "carol"}},
        {"approved": False, "state": None, "user": {"nickname": "bob"}},
    ],
}

# STOPPED is a failure and not a pending: a cancelled build is not one still running.
BITBUCKET_STATUSES = {
    "values": [
        {"key": "build", "state": "SUCCESSFUL"},
        {"key": "test", "state": "FAILED"},
        {"key": "e2e", "state": "STOPPED"},
        {"key": "deploy", "state": "INPROGRESS"},
    ]
}

BITBUCKET_QUERY = f"state=OPEN&q=source.branch.name%3D%22{BRANCH}%22&pagelen=50"

BITBUCKET_ROUTES: dict[str, Any] = {
    f"GET /2.0/repositories/{REPO}": webserver.json_ok({"mainbranch": {"name": "main"}}),
    f"GET /2.0/repositories/{REPO}/pullrequests/42": webserver.json_ok(BITBUCKET_PULL),
    f"GET /2.0/repositories/{REPO}/pullrequests/42/statuses?pagelen=100": webserver.json_ok(
        BITBUCKET_STATUSES
    ),
    f"GET /2.0/repositories/{REPO}/pullrequests?{BITBUCKET_QUERY}": webserver.json_ok(
        {"values": [BITBUCKET_PULL]}
    ),
    f"POST /2.0/repositories/{REPO}/pullrequests": webserver.json_ok(BITBUCKET_PULL, code=201),
    f"POST /2.0/repositories/{REPO}/pullrequests/42/comments": webserver.json_ok(
        {
            "id": 999,
            "links": {"html": {"href": "https://bitbucket.org/acme/widget/pull-requests/42#c999"}},
            "user": {"nickname": "atf-bot"},
        },
        code=201,
    ),
    f"GET /2.0/repositories/{REPO}/pullrequests/77": webserver.status(
        404, b'{"error":{"message":"Pull request not found"}}'
    ),
}

BITBUCKET = Forge(
    name="bitbucket",
    token_env="BITBUCKET_TOKEN",
    api_env="BITBUCKET_API_URL",
    remote=f"git@bitbucket.org:{REPO}.git",
    routes=BITBUCKET_ROUTES,
    mergeable=None,
    prefix="/2.0",
)

FORGES = [GITHUB, BITBUCKET]
