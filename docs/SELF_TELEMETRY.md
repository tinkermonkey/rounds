# Rounds Self-Telemetry

This document describes the OpenTelemetry instrumentation for the Rounds CLI itself, enabling observability of the diagnostic system's own operations.

## Overview

Rounds can now export telemetry data about its own operations using OpenTelemetry. This allows you to:

- Monitor CLI command performance and success rates
- Debug issues in the diagnostic system
- Track LLM costs and diagnosis durations
- Correlate errors across the diagnostic pipeline
- Observe poll cycle behavior and throughput

## Architecture

The self-telemetry implementation follows the OpenTelemetry specification:

- **Traces**: Distributed traces capture the full execution flow of CLI commands, diagnosis operations, and poll cycles
- **Spans**: Each operation (CLI command, diagnosis, scan) creates a span with relevant attributes
- **Exception Recording**: All errors are captured as span events with full exception details
- **Status Codes**: Spans are marked as OK or ERROR based on operation outcome

## Configuration

Self-telemetry is **disabled by default** to avoid overhead. Enable it by setting environment variables:

```bash
# Enable self-telemetry
ENABLE_SELF_TELEMETRY=true

# OTLP HTTP endpoint for exporting traces (required if enabled)
# Examples:
#   - SigNoz: http://localhost:4318/v1/traces
#   - Jaeger: http://localhost:4318/v1/traces
#   - Grafana Tempo: http://localhost:4318/v1/traces
SELF_TELEMETRY_OTLP_ENDPOINT=http://localhost:4318/v1/traces

# Service name (optional, default: rounds-cli)
SELF_TELEMETRY_SERVICE_NAME=rounds-cli

# Console export for debugging (optional, default: false)
SELF_TELEMETRY_CONSOLE_EXPORT=false
```

Add these to your `.env.rounds` file or set them in your environment.

## Instrumented Operations

### CLI Commands

All CLI commands are instrumented with spans:

| Command | Span Name | Key Attributes |
|---------|-----------|----------------|
| `list` | `cli.list_signatures` | `status_filter`, `output_format`, `result.count` |
| `details` | `cli.get_signature_details` | `signature_id`, `output_format` |
| `mute` | `cli.mute_signature` | `signature_id`, `reason` |
| `resolve` | `cli.resolve_signature` | `signature_id`, `fix_applied` |
| `retriage` | `cli.retriage_signature` | `signature_id` |
| `reinvestigate` | `cli.reinvestigate_signature` | `signature_id`, `diagnosis.confidence`, `diagnosis.cost_usd`, `diagnosis.model` |
| `investigate-trace` | `cli.investigate_trace` | `trace_id`, `investigation.cost_usd`, `investigation.model`, `investigation.services_count` |

### Main Operations

Top-level operations are also instrumented:

| Operation | Span Name | Key Attributes |
|-----------|-----------|----------------|
| Scan | `rounds.scan` | `result.new_signatures`, `result.updated_signatures`, `result.errors_processed`, `result.investigations_queued` |
| Diagnose | `rounds.diagnose` | `signature_id`, `signature.service`, `signature.error_type`, `diagnosis.confidence`, `diagnosis.cost_usd` |
| CLI Command Dispatch | `rounds.cli.command.{command}` | `command`, `signature_id` (if applicable), `result.status` |

### Error Handling

All exceptions are captured with:
- **Exception event**: Full exception details with type and message
- **Span status**: Set to ERROR with error description
- **Error attributes**: `error.type` set to exception class name
- **Log correlation**: Errors are logged with `exc_info=True` for full traceback

## Usage Examples

### Example 1: Basic Monitoring

Enable self-telemetry to monitor CLI command performance:

```bash
# .env.rounds
ENABLE_SELF_TELEMETRY=true
SELF_TELEMETRY_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

Run commands as usual:
```bash
docker compose -f docker-compose.rounds.yml exec rounds \
  python -m rounds.main cli-run list
```

View traces in your OTLP backend (SigNoz, Jaeger, Tempo) under service name `rounds-cli`.

### Example 2: Debugging with Console Export

Enable console export to see spans in logs:

```bash
# .env.rounds
ENABLE_SELF_TELEMETRY=true
SELF_TELEMETRY_CONSOLE_EXPORT=true
```

Spans will be printed to console with all attributes and events.

### Example 3: Cost Tracking

Monitor LLM costs by filtering spans with `diagnosis.cost_usd` attribute:

```
# In your OTLP backend query interface:
service.name = "rounds-cli" AND diagnosis.cost_usd > 0
```

Sum the `diagnosis.cost_usd` attribute across spans to track total spending.

### Example 4: Error Analysis

Find failed operations:

```
# In your OTLP backend:
service.name = "rounds-cli" AND status.code = ERROR
```

Group by `error.type` to identify common failure modes.

## Span Hierarchy

Typical span hierarchy for a CLI command:

```
rounds.cli.command.reinvestigate
├── cli.reinvestigate_signature
│   ├── management.reinvestigate (not instrumented yet)
│   │   ├── investigator.investigate (not instrumented yet)
│   │   │   ├── telemetry.get_traces (not instrumented yet)
│   │   │   ├── diagnosis.diagnose (not instrumented yet)
│   │   │   └── notification.notify (not instrumented yet)
│   │   └── store.update_signature (not instrumented yet)
```

**Note**: Only CLI and main entry points are currently instrumented. Future work will add instrumentation to core services and adapters.

## Performance Considerations

### Overhead

- **Minimal CPU overhead**: OpenTelemetry SDK uses efficient async processing
- **Batch export**: Spans are batched before export to reduce network calls
- **No blocking I/O**: Span export happens in background threads

### When to Enable

Enable self-telemetry when:
- Debugging issues in the rounds system
- Monitoring production deployment health
- Tracking LLM costs and diagnosis performance
- Correlating errors across operations

Disable self-telemetry when:
- Running in resource-constrained environments
- Minimizing latency for interactive CLI usage
- OTLP backend is unavailable or unreliable

### Resource Usage

Estimated resource overhead with self-telemetry enabled:
- CPU: < 5% additional usage
- Memory: ~10-20 MB for span batching
- Network: ~1-5 KB per operation (varies by span attribute size)

## Troubleshooting

### Spans Not Appearing

1. **Check OTLP endpoint**: Verify `SELF_TELEMETRY_OTLP_ENDPOINT` is correct
   ```bash
   curl -v http://localhost:4318/v1/traces
   ```

2. **Check telemetry backend**: Ensure your OTLP collector/backend is running
   ```bash
   # For SigNoz:
   docker ps | grep signoz
   ```

3. **Enable console export**: Set `SELF_TELEMETRY_CONSOLE_EXPORT=true` to see spans in logs

4. **Check logs**: Look for telemetry initialization messages:
   ```
   INFO - OpenTelemetry initialized for service: rounds-cli
   INFO - Self-telemetry enabled
   ```

### High Latency

If CLI commands are slow with telemetry enabled:

1. **Check OTLP endpoint latency**: Slow backend can delay span export
2. **Reduce batch size**: (Not configurable yet, contact maintainers)
3. **Disable console export**: Console export adds overhead
4. **Use async export**: (Default, no action needed)

### Missing Attributes

If span attributes are missing:

1. **Check span status**: Failed operations may have partial attributes
2. **Enable debug logging**: Set `LOG_LEVEL=DEBUG` to see span details
3. **Check for exceptions**: Errors may prevent attribute setting

## Future Enhancements

Planned improvements to self-telemetry:

- [ ] Instrument core services (Investigator, PollService, ManagementService)
- [ ] Instrument adapters (telemetry, store, diagnosis, notification)
- [ ] Add metrics (counters, gauges, histograms) for operations
- [ ] Add distributed context propagation (trace IDs in logs)
- [ ] Add sampling configuration for high-volume scenarios
- [ ] Add custom span processors for cost aggregation
- [ ] Add exemplars linking traces to metrics

## Integration with Existing Telemetry

The self-telemetry system is **independent** of the target application's telemetry:

- **Target telemetry**: Queried from SigNoz/Jaeger/Tempo for error diagnosis
- **Self-telemetry**: Exported to OTLP endpoint for rounds monitoring

You can use the **same OTLP backend** for both, but they appear as separate services:
- Target service: `my-app`, `my-api`, etc.
- Rounds service: `rounds-cli`

This enables correlation between target errors and diagnosis operations by trace ID.

## Security Considerations

Self-telemetry may expose sensitive information:

- **Signature IDs**: UUIDs that identify error patterns
- **Trace IDs**: May correlate with target application traces
- **Error messages**: May contain sensitive data from target application
- **File paths**: Codebase paths may leak directory structure

**Recommendations**:
1. Use private OTLP backend within your infrastructure
2. Enable authentication on OTLP endpoint
3. Sanitize error messages in target application
4. Use network policies to restrict OTLP access
5. Review span attributes before enabling in production

## Support

For issues or questions about self-telemetry:

1. Check logs with `LOG_LEVEL=DEBUG`
2. Enable console export for span details
3. File an issue at https://github.com/tinkermonkey/rounds/issues
4. Include relevant spans and logs (sanitize sensitive data)
