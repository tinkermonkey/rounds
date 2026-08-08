"""In-memory fake standing in for httpx.AsyncClient in defect-tracking tests.

GitHubIssueNotificationAdapter lazily creates its httpx.AsyncClient via
_get_client(), but exposes the resulting `_client` attribute as a test seam
(see tests/adapters/test_github_issues_notification.py, which assigns a
mock-transport-backed client directly). This fake is assigned the same way,
so the adapter's search/create/comment/close calls are served by an
in-memory GitHub Issues simulation instead of ever reaching the real API.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RecordedRequest:
    """A single API call made through FakeGitHubClient, for test assertions."""

    method: str
    url: str
    params: dict[str, Any] | None = None
    json: dict[str, Any] | None = None


@dataclass
class FakeIssue:
    """An in-memory GitHub issue tracked by FakeGitHubClient."""

    number: int
    title: str
    body: str
    labels: list[str]
    state: str = "open"
    comments: list[str] = field(default_factory=list)

    @property
    def html_url(self) -> str:
        return f"https://github.com/fake/fake/issues/{self.number}"


class FakeGitHubClient:
    """Simulates just enough of the GitHub Issues REST API for defect-tracking tests.

    Supports the four operations GitHubIssueNotificationAdapter performs:
    searching open issues by label, creating an issue, commenting on an
    issue, and closing an issue. All state lives in memory for the life of
    the test.
    """

    def __init__(self) -> None:
        self.issues: dict[int, FakeIssue] = {}
        self.requests: list[RecordedRequest] = []
        self._next_number = 1

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self.requests.append(RecordedRequest("GET", url, params=params))
        assert url.endswith("/issues"), f"Unsupported GET: {url}"
        request = httpx.Request("GET", f"https://api.github.com{url}", params=params)

        label = (params or {}).get("labels")
        state = (params or {}).get("state")
        matches = [
            issue
            for issue in self.issues.values()
            if (label is None or label in issue.labels)
            and (state is None or issue.state == state)
        ]
        payload = [{"number": issue.number, "html_url": issue.html_url} for issue in matches]
        return httpx.Response(200, json=payload, request=request)

    async def post(self, url: str, json: dict[str, Any] | None = None) -> httpx.Response:
        self.requests.append(RecordedRequest("POST", url, json=json))
        body = json or {}
        request = httpx.Request("POST", f"https://api.github.com{url}", json=json)

        if url.endswith("/comments"):
            issue_number = int(url.split("/issues/")[1].split("/comments")[0])
            issue = self.issues[issue_number]
            issue.comments.append(body.get("body", ""))
            return httpx.Response(201, json={"id": len(issue.comments)}, request=request)

        if url.endswith("/issues"):
            issue = FakeIssue(
                number=self._next_number,
                title=body.get("title", ""),
                body=body.get("body", ""),
                labels=list(body.get("labels", [])),
            )
            self.issues[issue.number] = issue
            self._next_number += 1
            return httpx.Response(
                201,
                json={"number": issue.number, "html_url": issue.html_url},
                request=request,
            )

        raise AssertionError(f"Unsupported POST: {url}")

    async def patch(self, url: str, json: dict[str, Any] | None = None) -> httpx.Response:
        self.requests.append(RecordedRequest("PATCH", url, json=json))
        request = httpx.Request("PATCH", f"https://api.github.com{url}", json=json)
        issue_number = int(url.rsplit("/", 1)[1])
        issue = self.issues[issue_number]
        state = (json or {}).get("state")
        if state:
            issue.state = state
        return httpx.Response(
            200, json={"number": issue.number, "state": issue.state}, request=request
        )

    async def aclose(self) -> None:
        """No-op: nothing to release for an in-memory fake."""
        return None
