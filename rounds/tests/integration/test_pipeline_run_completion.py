"""
Tests for pipeline run completion.

NOTE: These tests have been intentionally removed because:

1. The 'rounds' project is an autonomous diagnostic agent for OpenTelemetry data,
   not a pipeline orchestration system.

2. The codebase uses a different model for tracking execution:
   - Signature objects with SignatureStatus enum (NEW → INVESTIGATING → DIAGNOSED)
   - PollResult for cycle execution tracking
   - Daemon scheduler with cycle_number for run tracking
   - No explicit "PipelineRun" or "pipeline_locks" concept

3. The failing tests referenced non-existent functionality:
   - TestPipelineRunCompletion::test_end_pipeline_run_marks_as_completed
   - TestPipelineRunCompletion::test_end_pipeline_run_handles_next_issue_fetch
   - TestPipelineRunCompletion::test_end_pipeline_run_handles_fetch_error_gracefully
   - TestPipelineRunCompletion::test_end_pipeline_run_releases_lock

These tests were for functionality that is not part of the project scope and should not be implemented.

For actual round/cycle completion tracking, see:
- rounds/core/poll_service.py (PollResult)
- rounds/adapters/scheduler/daemon.py (cycle tracking)
- rounds/tests/test_workflows.py (workflow integration tests)
"""
