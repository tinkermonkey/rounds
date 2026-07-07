"""Tests for GitHubIssueNotificationAdapter.report() dedup, labeling, and recurrence."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus


def _make_adapter(transport: httpx.MockTransport) -> GitHubIssueNotificationAdapter:
    """Create a GitHubIssueNotificationAdapter with a mock transport."""
    adapter = GitHubIssueNotificationAdapter(
        repo_owner="acme",
        repo_name="widgets",
        github_token="test-token",
    )
    adapter._client = httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Authorization": "token test-token",
            "Accept": "application/vnd.github.v3+json",
        },
        transport=transport,
    )
    return adapter


def _make_signature(**overrides: Any) -> Signature:
    """Create a sample signature for testing."""
    defaults: dict[str, Any] = dict(
        id="sig-001",
        fingerprint="a" * 64,
        error_type="DatabaseError",
        service="api-service",
        message_template="Failed to connect to {database}",
        stack_hash="stack-abc",
        first_seen=datetime(2026, 7, 1, tzinfo=UTC),
        last_seen=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        occurrence_count=7,
        status=SignatureStatus.DIAGNOSED,
        max_severity=Severity.ERROR,
    )
    defaults.update(overrides)
    return Signature(**defaults)


def _make_diagnosis(**overrides: Any) -> Diagnosis:
    """Create a sample diagnosis for testing."""
    defaults: dict[str, Any] = dict(
        root_cause="Connection pool exhaustion",
        evidence=("Pool size exceeded",),
        suggested_fix="Increase pool size",
        confidence="high",
        diagnosed_at=datetime(2026, 7, 6, tzinfo=UTC),
        model="claude-opus",
        cost_usd=0.01,
    )
    defaults.update(overrides)
    return Diagnosis(**defaults)


class TestReportCreatePath:
    """Tests for report() when no existing open issue matches the fingerprint."""

    @pytest.mark.asyncio
    async def test_creates_issue_with_full_label_scheme(self) -> None:
        """No existing issue -> a new issue is created with the required labels."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        create_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(200, json=[])
            if request.method == "POST" and request.url.path.endswith("/issues"):
                payload = json.loads(request.content)
                create_calls.append(payload)
                return httpx.Response(
                    201,
                    json={"number": 42, "html_url": "https://github.com/acme/widgets/issues/42"},
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(create_calls) == 1
        labels = create_calls[0]["labels"]
        assert "rounds" in labels
        assert "auto-detected" in labels
        assert "severity-error" in labels
        assert "service-api-service" in labels
        assert f"fingerprint:{signature.fingerprint[:16]}" in labels

    @pytest.mark.asyncio
    async def test_search_filters_by_fingerprint_label_and_open_state(self) -> None:
        """The dedup search queries the issues endpoint with the fingerprint label."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        search_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                search_requests.append(request)
                return httpx.Response(200, json=[])
            if request.method == "POST" and request.url.path.endswith("/issues"):
                return httpx.Response(201, json={"number": 1, "html_url": "http://x"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(search_requests) == 1
        params = search_requests[0].url.params
        assert params["labels"] == f"fingerprint:{signature.fingerprint[:16]}"
        assert params["state"] == "open"

    @pytest.mark.asyncio
    async def test_issues_containing_pull_requests_are_ignored(self) -> None:
        """A matching 'issue' that is actually a pull request should not dedup."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        create_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(
                    200,
                    json=[{"number": 99, "pull_request": {"url": "http://x"}}],
                )
            if request.method == "POST" and request.url.path.endswith("/issues"):
                create_calls.append(request)
                return httpx.Response(201, json={"number": 100, "html_url": "http://x"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(create_calls) == 1


class TestReportDedupPath:
    """Tests for report() when an existing open issue matches the fingerprint."""

    @pytest.mark.asyncio
    async def test_posts_recurrence_comment_instead_of_creating_issue(self) -> None:
        """Existing open issue -> a recurrence comment is posted, no new issue created."""
        signature = _make_signature(occurrence_count=12)
        diagnosis = _make_diagnosis()
        create_calls = []
        comment_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(
                    200,
                    json=[{"number": 7, "html_url": "https://github.com/acme/widgets/issues/7"}],
                )
            if request.url.path.endswith("/issues/7/comments") and request.method == "POST":
                comment_calls.append(json.loads(request.content))
                return httpx.Response(201, json={"id": 1})
            if request.method == "POST" and request.url.path.endswith("/issues"):
                create_calls.append(request)
                return httpx.Response(201, json={"number": 100, "html_url": "http://x"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(create_calls) == 0
        assert len(comment_calls) == 1
        body = comment_calls[0]["body"]
        assert "12" in body
        assert signature.last_seen.isoformat() in body
        assert signature.service in body
        assert signature.max_severity.value in body

    @pytest.mark.asyncio
    async def test_recurrence_comment_http_error_raises(self) -> None:
        """A failure posting the recurrence comment should propagate."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(200, json=[{"number": 7, "html_url": "http://x"}])
            if request.url.path.endswith("/issues/7/comments"):
                return httpx.Response(500, json={"error": "boom"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.report(signature, diagnosis)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_malformed_json_response_raises_with_context(self) -> None:
        """Malformed JSON from the issue search endpoint raises a decode error."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(200, content=b"not valid json")
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(json.JSONDecodeError):
            await adapter.report(signature, diagnosis)
        await adapter.close()


class TestCloseResolvedIssue:
    """Tests for close_resolved_issue() auto-close behavior."""

    @pytest.mark.asyncio
    async def test_closes_open_issue_with_resolution_comment(self) -> None:
        """An open issue matching the fingerprint is closed and commented on."""
        signature = _make_signature(status=SignatureStatus.RESOLVED)
        call_order = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(
                    200,
                    json=[{"number": 7, "html_url": "https://github.com/acme/widgets/issues/7"}],
                )
            if request.method == "POST" and request.url.path.endswith("/issues/7/comments"):
                call_order.append(("comment", json.loads(request.content)))
                return httpx.Response(201, json={"id": 1})
            if request.method == "PATCH" and request.url.path.endswith("/issues/7"):
                call_order.append(("close", json.loads(request.content)))
                return httpx.Response(200, json={"number": 7, "state": "closed"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.close_resolved_issue(signature)
        await adapter.close()

        assert [kind for kind, _ in call_order] == ["close", "comment"]
        assert call_order[0][1] == {"state": "closed"}
        assert "Auto-Resolved" in call_order[1][1]["body"]

    @pytest.mark.asyncio
    async def test_no_open_issue_is_a_no_op(self) -> None:
        """No matching open issue -> nothing is closed or commented on."""
        signature = _make_signature(status=SignatureStatus.RESOLVED)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(200, json=[])
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.close_resolved_issue(signature)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_close_failure_propagates_without_posting_comment(self) -> None:
        """If closing the issue fails, no resolution comment is posted.

        Regression test: closing must happen before the comment so a failed
        close never leaves the issue open with a comment falsely claiming it
        was closed.
        """
        signature = _make_signature(status=SignatureStatus.RESOLVED)
        comment_posted = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal comment_posted
            if request.method == "GET" and request.url.path.endswith("/issues"):
                return httpx.Response(200, json=[{"number": 7, "html_url": "http://x"}])
            if request.method == "POST" and request.url.path.endswith("/issues/7/comments"):
                comment_posted = True
                return httpx.Response(201, json={"id": 1})
            if request.method == "PATCH" and request.url.path.endswith("/issues/7"):
                return httpx.Response(500, json={"error": "boom"})
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.close_resolved_issue(signature)
        await adapter.close()

        assert comment_posted is False
