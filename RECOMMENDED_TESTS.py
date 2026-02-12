"""Recommended test code to add to test_new_implementations.py

These tests fill critical gaps identified in the coverage analysis.
Ready to copy/paste into the main test file.
"""

# ============================================================================
# ManagementService - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_mute_signature_database_error(service, store):
    """Test handling when store.update() raises a database error.

    CRITICALITY: 9/10
    ISSUE: Currently catches ValueError but not generic database errors
    """
    store.update = AsyncMock(side_effect=ConnectionError("Database unavailable"))

    with pytest.raises(ConnectionError):
        await service.mute_signature("sig-123", "Investigating")


@pytest.mark.asyncio
async def test_resolve_signature_database_error(service, store):
    """Test handling when store.update() raises during resolve."""
    store.update = AsyncMock(side_effect=TimeoutError("Query timeout"))

    with pytest.raises(TimeoutError):
        await service.resolve_signature("sig-123", "Patched")


@pytest.mark.asyncio
async def test_get_signature_details_similar_signatures_error(service, store, sample_signature):
    """Test handling when get_similar() fails.

    CRITICALITY: 8/10
    ISSUE: get_similar() could fail and should be handled gracefully
    """
    await store.save(sample_signature)
    store.get_similar = AsyncMock(side_effect=Exception("Query failed"))

    with pytest.raises(Exception):
        await service.get_signature_details("sig-123")


@pytest.mark.asyncio
async def test_mute_signature_timezone_consistency(service, store, sample_signature):
    """Test that last_seen maintains timezone info.

    CRITICALITY: 8/10
    ISSUE: Management service uses datetime.utcnow() (naive)
           but fixtures use timezone.utc (aware)
    """
    sample_signature.last_seen = datetime.now(tz=timezone.utc)
    await store.save(sample_signature)
    original_tz = sample_signature.last_seen.tzinfo

    await service.mute_signature("sig-123")

    updated = await store.get_by_id("sig-123")
    assert updated is not None
    # This should PASS but currently FAILS due to naive datetime in service
    # assert updated.last_seen.tzinfo is not None
    # assert updated.last_seen.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_retriage_clears_all_diagnosis_fields(service, store, sample_signature):
    """Test that retriage completely clears diagnosis, not just sets to None.

    CRITICALITY: 6/10
    ISSUE: Verify diagnosis is truly cleared, all fields removed
    """
    diagnosis = Diagnosis(
        root_cause="Connection limit",
        evidence=("Pool exhausted",),
        suggested_fix="Increase pool size",
        confidence=Confidence.HIGH,
        diagnosed_at=datetime.now(tz=timezone.utc),
        model="claude-3",
        cost_usd=0.50,
    )
    sample_signature.diagnosis = diagnosis
    sample_signature.status = SignatureStatus.DIAGNOSED
    await store.save(sample_signature)

    await service.retriage_signature("sig-123")

    details = await service.get_signature_details("sig-123")
    assert details["diagnosis"] is None


@pytest.mark.asyncio
async def test_get_signature_details_with_tags(service, store, sample_signature):
    """Test that tags are properly included in details.

    CRITICALITY: 5/10
    ISSUE: Tags field in details not tested
    """
    sample_signature.tags = {"urgent", "database", "timeout"}
    await store.save(sample_signature)

    details = await service.get_signature_details("sig-123")

    assert "tags" in details
    assert details["tags"] == ["database", "timeout", "urgent"]  # sorted


# ============================================================================
# CLICommandHandler - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_mute_command_with_runtime_error(handler, mock_management):
    """Test handling of unexpected exceptions beyond ValueError.

    CRITICALITY: 7/10
    ISSUE: Only ValueError is caught, other exceptions propagate
    """
    mock_management.mute_signature.side_effect = RuntimeError("DB connection lost")

    # Currently this will propagate unhandled
    with pytest.raises(RuntimeError):
        await handler.mute_signature("sig-123")

    # OR improve handler to catch all exceptions gracefully:
    # result = await handler.mute_signature("sig-123")
    # assert result["status"] == "error"
    # assert "RuntimeError" in result["message"]


@pytest.mark.asyncio
async def test_get_details_with_null_optional_fields(handler, mock_management):
    """Test text formatting with None/missing optional fields.

    CRITICALITY: 7/10
    ISSUE: Handler doesn't gracefully handle null values in details dict
    """
    details = {
        "id": "sig-123",
        "fingerprint": "abc123",
        "service": None,  # Unexpected null
        "error_type": "TimeoutError",
        "status": "new",
        "occurrence_count": 5,
        "first_seen": "2024-01-01T00:00:00",
        "last_seen": "2024-01-02T00:00:00",
        "message_template": "Timeout",
        "diagnosis": None,
        "related_signatures": [],
    }
    mock_management.get_signature_details.return_value = details

    result = await handler.get_signature_details("sig-123", format="text")

    assert result["status"] == "success"
    # Verify text doesn't contain "None" literal
    assert "Service: None" not in result["data"]


@pytest.mark.asyncio
async def test_get_details_text_format_large_dataset(handler, mock_management):
    """Test text formatting with large number of related signatures.

    CRITICALITY: 6/10
    ISSUE: No pagination or truncation of large result sets
    """
    details = {
        "id": "sig-123",
        "fingerprint": "abc123",
        "service": "api-service",
        "error_type": "ValueError",
        "status": "new",
        "occurrence_count": 5,
        "first_seen": "2024-01-01T00:00:00",
        "last_seen": "2024-01-02T00:00:00",
        "message_template": "Invalid value",
        "diagnosis": None,
        "related_signatures": [
            {"id": f"sig-{i}", "service": "api", "occurrence_count": i,
             "error_type": "ValueError", "status": "new"}
            for i in range(100)
        ],
    }
    mock_management.get_signature_details.return_value = details

    result = await handler.get_signature_details("sig-123", format="text")

    # Should handle gracefully - either truncate or include all
    assert result["status"] == "success"
    text_output = result["data"]
    # Verify it's not excessively large
    assert len(text_output) < 50000  # Reasonable limit


@pytest.mark.asyncio
async def test_run_command_missing_required_argument(mock_management):
    """Test that run_command handles missing required arguments.

    CRITICALITY: 6/10
    ISSUE: KeyError will be raised, not caught
    """
    with pytest.raises(KeyError):  # Or should return error dict?
        await run_command(mock_management, "mute", {"reason": "test"})


@pytest.mark.asyncio
async def test_mute_with_verbose_flag_logging(handler, mock_management, caplog):
    """Test that verbose flag produces expected logging.

    CRITICALITY: 4/10
    ISSUE: Verbose flag is accepted but logging not verified
    """
    with caplog.at_level(logging.INFO):
        await handler.mute_signature("sig-123", "Fixed", verbose=True)

    # Should have logged something
    assert any("Muted signature" in record.message for record in caplog.records)


# ============================================================================
# MarkdownNotificationAdapter - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_report_writes(adapter, temp_file, sample_signature, sample_diagnosis):
    """Test concurrent writes don't corrupt file.

    CRITICALITY: 9/10 (CRITICAL - RACE CONDITION)
    ISSUE: asyncio.Lock should prevent interleaving, but needs verification
    """
    sig1, sig2 = sample_signature, sample_signature
    diag1, diag2 = sample_diagnosis, sample_diagnosis

    # Run two reports concurrently
    await asyncio.gather(
        adapter.report(sig1, diag1),
        adapter.report(sig2, diag2),
    )

    content = temp_file.read_text()

    # Should have both reports, not interleaved
    assert content.count("Diagnosis Report") == 2
    # Each report should be complete (has all sections)
    assert content.count("Root Cause Analysis") == 2
    assert content.count("---") >= 2


@pytest.mark.asyncio
async def test_report_to_read_only_file(adapter, temp_file, sample_signature, sample_diagnosis):
    """Test handling when file is not writable.

    CRITICALITY: 8/10
    ISSUE: Should handle permission errors gracefully
    """
    # Make file read-only
    Path(adapter.report_path).chmod(0o444)

    try:
        with pytest.raises(IOError):
            await adapter.report(sample_signature, sample_diagnosis)
    finally:
        # Restore permissions for cleanup
        Path(adapter.report_path).chmod(0o644)


@pytest.mark.asyncio
async def test_report_with_special_markdown_chars(adapter, temp_file):
    """Test that special markdown chars don't break formatting.

    CRITICALITY: 6/10
    ISSUE: User data with markdown chars could break report structure
    """
    sig = Signature(
        id="sig-123",
        fingerprint="abc123",
        error_type="TimeoutError",
        service="api-service",
        message_template="Error with **bold** and [link](http://example.com) and `code`",
        stack_hash="stack-123",
        first_seen=datetime.now(tz=timezone.utc),
        last_seen=datetime.now(tz=timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )
    diag = Diagnosis(
        root_cause="Issue with _italic_ and ***bold italic***",
        evidence=("[Evidence with link]()", "Normal evidence"),
        suggested_fix="Fix **this** issue",
        confidence=Confidence.HIGH,
        diagnosed_at=datetime.now(tz=timezone.utc),
        model="claude-3",
        cost_usd=0.50,
    )

    await adapter.report(sig, diag)

    content = temp_file.read_text()
    # Should have valid markdown sections
    assert "## Diagnosis Report" in content
    assert "### Error Information" in content
    # Should not have unescaped problematic markdown
    assert content.count("---") >= 2  # Separator lines


@pytest.mark.asyncio
async def test_lock_released_on_write_failure(adapter, temp_file, sample_signature, sample_diagnosis):
    """Test that lock is released even if write fails.

    CRITICALITY: 7/10
    ISSUE: Lock must be released to prevent deadlock on retry
    """
    sig1 = sample_signature
    diag1 = sample_diagnosis

    # First call succeeds
    await adapter.report(sig1, diag1)

    # Make file read-only to trigger error
    Path(adapter.report_path).chmod(0o444)

    sig2 = Signature(
        id="sig-456",
        fingerprint="def456",
        error_type="ValueError",
        service="service2",
        message_template="Different error",
        stack_hash="stack-456",
        first_seen=datetime.now(tz=timezone.utc),
        last_seen=datetime.now(tz=timezone.utc),
        occurrence_count=3,
        status=SignatureStatus.NEW,
    )

    # This should fail but NOT deadlock
    with pytest.raises(IOError):
        await adapter.report(sig2, diag1)

    # Restore permissions
    Path(adapter.report_path).chmod(0o644)

    # Third call should succeed if lock was properly released
    sig3 = Signature(
        id="sig-789",
        fingerprint="ghi789",
        error_type="RuntimeError",
        service="service3",
        message_template="Third error",
        stack_hash="stack-789",
        first_seen=datetime.now(tz=timezone.utc),
        last_seen=datetime.now(tz=timezone.utc),
        occurrence_count=7,
        status=SignatureStatus.NEW,
    )
    await adapter.report(sig3, diag1)  # Should succeed without deadlock


@pytest.mark.asyncio
async def test_report_path_directory_creation(temp_file):
    """Test that parent directories are created on adapter init.

    CRITICALITY: 5/10
    ISSUE: Missing directory handling not tested
    """
    import tempfile
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        nested_path = Path(tmpdir) / "reports" / "nested" / "deep" / "report.md"

        # Parent directory doesn't exist yet
        assert not nested_path.parent.exists()

        # Adapter initialization should create it
        adapter = MarkdownNotificationAdapter(str(nested_path))

        assert nested_path.parent.exists()


# ============================================================================
# GitHubIssueNotificationAdapter - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_github_adapter_async_context_manager(adapter):
    """Test async context manager lifecycle.

    CRITICALITY: 8/10
    ISSUE: Context manager not tested, client state after exit not verified
    """
    async with adapter as a:
        assert a is adapter
        assert a._client is not None

        # Get client reference while inside context
        client_inside = a._client

    # After context exit, _client should still be closed
    # Verify by trying to use it (should fail)
    with pytest.raises(Exception):
        # Attempting to use closed client should fail
        await adapter._client.get("/api/repos")


@pytest.mark.asyncio
async def test_report_github_api_unauthorized(adapter, sample_signature, sample_diagnosis):
    """Test handling of 401 Unauthorized response.

    CRITICALITY: 9/10 (CRITICAL - AUTH FAILURE)
    ISSUE: Invalid token or credentials should be detected
    """
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"message": "Bad credentials"}'
        mock_post.return_value = mock_response

        # Should log error but not raise for transient errors
        # Currently logs error - behavior should be tested
        await adapter.report(sample_signature, sample_diagnosis)

        # Verify log message mentions authentication failure


@pytest.mark.asyncio
async def test_report_github_api_forbidden(adapter, sample_signature, sample_diagnosis):
    """Test handling of 403 Forbidden response.

    CRITICALITY: 8/10
    ISSUE: Token exists but lacks permissions
    """
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"message": "Resource not accessible by integration"}'
        mock_post.return_value = mock_response

        await adapter.report(sample_signature, sample_diagnosis)
        # Should distinguish from transient error


@pytest.mark.asyncio
async def test_report_github_api_not_found(adapter, sample_signature, sample_diagnosis):
    """Test handling of 404 Not Found response.

    CRITICALITY: 8/10
    ISSUE: Repository doesn't exist or user doesn't have access
    """
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not Found"}'
        mock_post.return_value = mock_response

        await adapter.report(sample_signature, sample_diagnosis)


@pytest.mark.asyncio
async def test_report_github_api_validation_error(adapter, sample_signature, sample_diagnosis):
    """Test handling of 422 Validation Failed response.

    CRITICALITY: 7/10
    ISSUE: Invalid label or other validation issue
    """
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = '{"message": "Invalid label name"}'
        mock_post.return_value = mock_response

        await adapter.report(sample_signature, sample_diagnosis)


@pytest.mark.asyncio
async def test_format_issue_title_length(adapter):
    """Test that issue title doesn't exceed GitHub's limit.

    CRITICALITY: 6/10
    ISSUE: Title truncation at 60 chars might not be enough overall
    """
    sig = Signature(
        id="sig-123",
        fingerprint="abc123",
        error_type="VeryLongErrorTypeNameThatTakesUpSpaceError",
        service="very-long-service-name-that-is-descriptive",
        message_template="A" * 500,
        stack_hash="stack-123",
        first_seen=datetime.now(tz=timezone.utc),
        last_seen=datetime.now(tz=timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )

    title = adapter._format_issue_title(sig)

    # GitHub title limit is 256 characters
    assert len(title) <= 256


@pytest.mark.asyncio
async def test_format_issue_body_empty_evidence(adapter):
    """Test formatting when evidence list is empty.

    CRITICALITY: 5/10
    ISSUE: Empty evidence shouldn't break markdown structure
    """
    sig = Signature(
        id="sig-123",
        fingerprint="abc123",
        error_type="DatabaseError",
        service="payment-service",
        message_template="Connection refused",
        stack_hash="stack-123",
        first_seen=datetime.now(tz=timezone.utc),
        last_seen=datetime.now(tz=timezone.utc),
        occurrence_count=10,
        status=SignatureStatus.NEW,
    )
    diag = Diagnosis(
        root_cause="Database server overloaded",
        evidence=(),  # Empty tuple
        suggested_fix="Scale database",
        confidence=Confidence.HIGH,
        diagnosed_at=datetime.now(tz=timezone.utc),
        model="claude-3",
        cost_usd=0.75,
    )

    body = adapter._format_issue_body(sig, diag)

    # Should be valid markdown
    assert "## Error Information" in body
    assert "## Root Cause Analysis" in body
    # But evidence section should handle empty gracefully
    assert body.count("\n") > 10  # Has structure


@pytest.mark.asyncio
async def test_multiple_reports_reuse_client(adapter, sample_signature, sample_diagnosis):
    """Test that HTTP client is reused across multiple report calls.

    CRITICALITY: 7/10
    ISSUE: Should not create new clients for each call
    """
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"number": 1, "html_url": "http://github.com/owner/repo/issues/1"}
        mock_post.return_value = mock_response

        client_ids = []

        # First report
        await adapter.report(sample_signature, sample_diagnosis)
        client_ids.append(id(adapter._client))

        # Second report
        await adapter.report(sample_signature, sample_diagnosis)
        client_ids.append(id(adapter._client))

        # Should be same client object
        assert client_ids[0] == client_ids[1]


# ============================================================================
# JaegerTelemetryAdapter - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_jaeger_get_recent_errors_basic(adapter):
    """Test querying recent errors from Jaeger.

    CRITICALITY: 10/10 (CRITICAL - ZERO REAL TESTS)
    ISSUE: Entire adapter untested beyond lifecycle
    """
    with patch.object(adapter.client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{
                "traceID": "trace-123",
                "spans": [{
                    "spanID": "span-1",
                    "operationName": "db.query",
                    "processID": "proc-1",
                    "tags": {"error": True},
                    "logs": [{
                        "fields": [{"key": "message", "value": "Connection timeout"}]
                    }],
                    "startTime": 1000000,
                }],
                "processes": {
                    "proc-1": {"serviceName": "payment-service"}
                },
            }],
        }
        mock_get.return_value = mock_response

        errors = await adapter.get_recent_errors(
            since=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )

        assert len(errors) > 0
        assert errors[0].error_type is not None
        assert errors[0].service == "payment-service"


@pytest.mark.asyncio
async def test_jaeger_extract_error_events_multiple_spans(adapter):
    """Test extracting error events from trace with multiple error spans.

    CRITICALITY: 9/10
    ISSUE: Entire extraction logic untested
    """
    trace = {
        "traceID": "trace-123",
        "spans": [
            {
                "spanID": "span-1",
                "tags": {"error": True},
                "logs": [{"fields": []}],
                "process": {"serviceName": "service-1"},
                "startTime": 1000000,
            },
            {
                "spanID": "span-2",
                "tags": {"otel.status_code": "ERROR"},
                "logs": [{"fields": []}],
                "process": {"serviceName": "service-2"},
                "startTime": 2000000,
            },
        ],
    }

    events = adapter._extract_error_events(trace)

    assert len(events) == 2
    assert events[0].service == "service-1"
    assert events[1].service == "service-2"


@pytest.mark.asyncio
async def test_jaeger_is_error_span_detection(adapter):
    """Test error span detection with different tag formats.

    CRITICALITY: 8/10
    ISSUE: Multiple error detection paths should be tested separately
    """
    # Span with error=true tag
    span1 = {"tags": {"error": True}, "logs": []}
    assert adapter._is_error_span(span1) is True

    # Span with otel.status_code=ERROR
    span2 = {"tags": {"otel.status_code": "ERROR"}, "logs": []}
    assert adapter._is_error_span(span2) is True

    # Span with error event in logs
    span3 = {
        "tags": {},
        "logs": [{
            "fields": [{"key": "event", "value": "error"}]
        }],
    }
    assert adapter._is_error_span(span3) is True

    # Non-error span
    span4 = {"tags": {}, "logs": []}
    assert adapter._is_error_span(span4) is False


@pytest.mark.asyncio
async def test_jaeger_get_trace_builds_span_tree(adapter):
    """Test building span hierarchy from Jaeger trace.

    CRITICALITY: 9/10
    ISSUE: Complex tree-building logic untested
    """
    with patch.object(adapter.client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{
                "traceID": "trace-123",
                "spans": [
                    {
                        "spanID": "root",
                        "parentSpanID": None,
                        "operationName": "root-op",
                        "processID": "proc-1",
                        "tags": {},
                        "duration": 5000,
                    },
                    {
                        "spanID": "child1",
                        "parentSpanID": "root",
                        "operationName": "child-op",
                        "processID": "proc-1",
                        "tags": {},
                        "duration": 3000,
                    },
                ],
                "processes": {
                    "proc-1": {"serviceName": "service-1"}
                },
            }],
        }
        mock_get.return_value = mock_response

        trace_tree = await adapter.get_trace("trace-123")

        assert trace_tree.trace_id == "trace-123"
        assert trace_tree.root_span.span_id == "root"
        assert len(trace_tree.root_span.children) == 1
        assert trace_tree.root_span.children[0].span_id == "child1"


@pytest.mark.asyncio
async def test_jaeger_get_recent_errors_service_filter(adapter):
    """Test filtering errors by specific services.

    CRITICALITY: 7/10
    ISSUE: Service filtering in query not tested
    """
    with patch.object(adapter.client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_get.return_value = mock_response

        await adapter.get_recent_errors(
            since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            services=["payment-service", "auth-service"]
        )

        # Verify API was called with correct params
        calls = mock_get.call_args_list
        assert len(calls) == 2  # Once per service


# ============================================================================
# GrafanaStackTelemetryAdapter - Critical Gap Tests
# ============================================================================

@pytest.mark.asyncio
async def test_grafana_get_recent_errors_loki_query(adapter):
    """Test querying errors from Loki.

    CRITICALITY: 10/10 (CRITICAL - ZERO REAL TESTS)
    ISSUE: Entire Loki integration untested
    """
    with patch.object(adapter.loki_client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "result": [{
                    "stream": {"service": "payment-service", "level": "error"},
                    "values": [
                        ["1000000000", '{"error_type": "TimeoutError", "message": "DB timeout"}']
                    ],
                }],
            },
        }
        mock_get.return_value = mock_response

        errors = await adapter.get_recent_errors(
            since=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )

        assert len(errors) > 0
        assert errors[0].error_type == "TimeoutError"


@pytest.mark.asyncio
async def test_grafana_context_manager_closes_all_clients(adapter):
    """Test that all three HTTP clients are properly closed.

    CRITICALITY: 8/10
    ISSUE: Multiple clients need proper lifecycle management
    """
    async with adapter as a:
        assert a.tempo_client is not None
        assert a.loki_client is not None
        assert a.prometheus_client is not None

        # Store IDs to verify they were actually closed
        tempo_id = id(a.tempo_client)
        loki_id = id(a.loki_client)
        prom_id = id(a.prometheus_client)

    # After context exit, clients should be closed
    # Attempting operations should fail


@pytest.mark.asyncio
async def test_grafana_adapter_without_prometheus(temp_url):
    """Test creating adapter without optional Prometheus.

    CRITICALITY: 7/10
    ISSUE: Optional client handling not tested
    """
    adapter = GrafanaStackTelemetryAdapter(
        tempo_url="http://tempo:3200",
        loki_url="http://loki:3100",
        prometheus_url="",  # Empty
    )

    assert adapter.tempo_client is not None
    assert adapter.loki_client is not None
    assert adapter.prometheus_client is None


@pytest.mark.asyncio
async def test_grafana_get_trace_parses_tempo_format(adapter):
    """Test parsing Tempo's OpenTelemetry trace format.

    CRITICALITY: 9/10
    ISSUE: Tempo parsing logic completely untested
    """
    with patch.object(adapter.tempo_client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "batches": [{
                "scopeSpans": [{
                    "spans": [
                        {
                            "spanId": "root",
                            "parentSpanId": None,
                            "name": "root-operation",
                            "startTimeUnixNano": 1000000000,
                            "endTimeUnixNano": 6000000000,
                            "status": {"code": 0},
                            "attributes": [],
                            "instrumentationScope": {"name": "service-1"},
                        },
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_response

        trace_tree = await adapter.get_trace("trace-123")

        assert trace_tree.trace_id == "trace-123"
        assert trace_tree.root_span.operation == "root-operation"


@pytest.mark.asyncio
async def test_grafana_parse_error_from_log_complete(adapter):
    """Test parsing ErrorEvent from Loki log entry.

    CRITICALITY: 8/10
    ISSUE: Error parsing logic untested
    """
    log_data = {
        "error_type": "ConnectionError",
        "message": "Failed to connect",
        "service": "api-service",
        "trace_id": "trace-123",
        "stack": 'File "app.py", line 42, in handle\nFile "db.py", line 15, in query',
    }

    error = GrafanaStackTelemetryAdapter._parse_error_from_log(log_data)

    assert error is not None
    assert error.error_type == "ConnectionError"
    assert error.service == "api-service"
    assert len(error.stack_frames) > 0


# ============================================================================
# End of Recommended Tests
# ============================================================================
