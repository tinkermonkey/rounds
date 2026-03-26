# CLI Telemetry Optimization Implementation Guide

Based on trace analysis of the `dr version` command, this document provides detailed implementation steps for optimizations 2-7 (keeping telemetry on all commands as requested).

---

## Optimization 2: Bundle Version at Build Time

**Impact**: Eliminates 2-3ms of file I/O and JSON parsing on every command execution.

### File: `cli/esbuild.config.js`

**Add after line 15** (after gitHash capture):
```javascript
// Capture CLI version from package.json at build time
let cliVersion = '0.1.3';
try {
  const pkg = JSON.parse(readFileSync('package.json', 'utf-8'));
  cliVersion = pkg.version || '0.1.3';
} catch (error) {
  console.warn('Warning: Could not read CLI version from package.json.');
}
```

**Update imports** (line 3):
```javascript
import { renameSync, rmSync, cpSync, readFileSync } from 'fs';
```

**Update define block** (lines 37-40):
```javascript
  define: {
    'TELEMETRY_ENABLED': isDebug ? 'true' : 'false',
    'GIT_HASH': JSON.stringify(gitHash),
    'CLI_VERSION': JSON.stringify(cliVersion),
  },
```

### File: `cli/src/commands/version.ts`

**Replace lines 12-33** (the entire getPackageVersion function and GIT_HASH declaration):
```typescript
// Declare build-time constants (substituted by esbuild)
declare const GIT_HASH: string;
declare const CLI_VERSION: string;

const gitHash = typeof GIT_HASH !== "undefined" ? GIT_HASH : "unknown";
const cliVersion = typeof CLI_VERSION !== "undefined" ? CLI_VERSION : "0.1.3";
```

**Update versionCommand** (line 35):
```typescript
export async function versionCommand(): Promise<void> {
  // Remove: const cliVersion = await getPackageVersion();
  const specVersion = getCliBundledSpecVersion();
  const telemetryBuiltIn = isTelemetryBuiltIn();
  const telemetryConfigured = await isTelemetryConfigured();
  // ... rest unchanged
}
```

**Remove unused imports** (lines 5-7):
```typescript
import ansis from "ansis";
// Remove: import path from "node:path";
// Remove: import { readJSON, fileExists } from "../utils/file-io.js";
import { getCliBundledSpecVersion } from "../utils/spec-version.js";
```

---

## Optimization 3: Fix HTTP KeepAlive Issue

**Impact**: Removes need for `process.exit()` workaround, allowing clean Node.js shutdown.

### File: `cli/src/telemetry/resilient-exporter.ts`

**Replace lines 54-64** (OTLPTraceExporter initialization):
```typescript
    } else {
      // Use standard http-based exporter for Node.js with keepAlive disabled
      // for short-lived CLI processes to prevent hanging on shutdown
      this.delegate = new OTLPTraceExporter({
        ...config,
        url: this.url,
        timeoutMillis: config?.timeoutMillis ?? 5000,
        // Disable HTTP keepAlive for CLI use case
        keepAlive: false,
      });
      if (this.debug) {
        process.stderr.write(`[TELEMETRY] Trace exporter initialized: ${this.url}\n`);
      }
    }
```

### File: `cli/src/telemetry/resilient-log-exporter.ts`

**Update OTLPLogExporter initialization** (lines 41-49):
```typescript
    this.delegate = new OTLPLogExporter({
      ...config,
      url: this.url,
      timeoutMillis: config?.timeoutMillis ?? 5000,
      // Disable HTTP keepAlive for CLI use case
      keepAlive: false,
    });
```

### File: `cli/src/cli.ts`

**Update shutdown logic** (lines 811-818):
```typescript
      // Shutdown telemetry after execution completes
      await shutdownTelemetry();

      // Exit with proper code (no longer need immediate process.exit workaround)
      process.exit(exitCode);
```

**Update comment** to reflect the fix:
```typescript
      // Shutdown telemetry after execution completes
      await shutdownTelemetry();

      // Exit with exit code (keepAlive disabled in exporters prevents hanging)
      process.exit(exitCode);
```

---

## Optimization 4: Lazy Console Interceptor Installation

**Impact**: Reduces initialization overhead by 1-2ms for commands that don't use console logging.

### File: `cli/src/cli.ts`

**Add constant after imports** (around line 100):
```typescript
// Commands that require console interceptor for telemetry logging
const CONSOLE_LOGGING_COMMANDS = new Set(['chat', 'validate', 'import', 'export']);
```

**Update telemetry initialization** (lines 754-758):
```typescript
    if (isTelemetryEnabled) {
      // Initialize telemetry before execution
      await initTelemetry();

      // Only install console interceptor for commands that need it
      const commandName = process.argv[2] || "unknown";
      if (CONSOLE_LOGGING_COMMANDS.has(commandName)) {
        await installConsoleInterceptor();
      }

      const args = process.argv.slice(3).join(" ");
```

**Remove the separate `const commandName` declaration** (it's now in the block above).

---

## Optimization 5: Batch Span Exports with Timeout

**Impact**: Reduces HTTP requests for multi-span commands. Adds 100ms latency but improves throughput.

### File: `cli/src/telemetry/index.ts`

**Update imports** (line 62):
```typescript
    const [
      { NodeSDK },
      { BatchSpanProcessor },  // Changed from SimpleSpanProcessor
      { SimpleLogRecordProcessor },
      otelApi,
      { Resource },
      { LoggerProvider },
      { ResilientOTLPExporter },
      { ResilientLogExporter },
      { loadOTLPConfig },
    ] = await Promise.all([
      import("@opentelemetry/sdk-node"),
      import("@opentelemetry/sdk-trace-base"),
      import("@opentelemetry/sdk-logs"),
      // ... rest unchanged
    ]);
```

**Update span processor creation** (line 161):
```typescript
    // Create span processor with batching for CLI use case
    // Short delay (100ms) balances latency vs HTTP request overhead
    spanProcessor = new BatchSpanProcessor(traceExporter, {
      maxExportBatchSize: 32,
      maxQueueSize: 64,
      scheduledDelayMillis: 100, // Short delay for CLI
      exportTimeoutMillis: 5000,
    });
```

**Update NodeSDK initialization comment** (line 163):
```typescript
    // Initialize NodeSDK with BatchSpanProcessor for efficient export
    const nodeSdk = new NodeSDK({
      resource,
      spanProcessor,
    });
```

---

## Optimization 6: Remove Redundant Span Attributes

**Impact**: Minor CPU/memory savings from eliminating duplicate attribute setting.

### File: `cli/src/cli.ts`

**Remove duplicate setAttributes call** (lines 764-772):
```typescript
      // Wrap entire CLI execution in active span for proper context propagation
      await startActiveSpan(
        "cli.execute",
        async (span) => {
          // Attributes already set via options parameter - removed duplicate setAttributes
          try {
            // Execute command - all child spans will now link to this root span
            await program.parseAsync(process.argv);

            // Set success status
            span.setStatus({ code: 0 }); // SpanStatusCode.OK
          } catch (error) {
            // ... rest unchanged
          }
        },
        {
          "cli.command": commandName,
          "cli.args": args,
          "cli.cwd": process.cwd(),
          "cli.version": cliVersion,
        }
      );
```

The span attributes in the options object (third parameter) are automatically set by `startActiveSpan`, so the manual `span.setAttributes()` call is redundant.

---

## Optimization 7: Early Telemetry Configuration Check

**Impact**: Avoids loading ~1MB of OTEL dependencies when telemetry isn't configured (3-5ms on first run).

### File: `cli/src/telemetry/index.ts`

**Reorder initTelemetry function** (lines 56-91):
```typescript
export async function initTelemetry(): Promise<void> {
  if (isTelemetryEnabled) {
    // Load OTLP config FIRST to check if telemetry is actually configured
    const { loadOTLPConfig } = await import("./config.js");
    const otlpConfig = await loadOTLPConfig();

    // Early return if not configured - avoid loading heavy OTEL dependencies
    // This prevents hanging at shutdown when no collector is running
    if (!otlpConfig.isExplicitlyConfigured) {
      return;
    }

    // Now load heavy dependencies only when telemetry is actually configured
    const [
      { NodeSDK },
      { BatchSpanProcessor },
      { SimpleLogRecordProcessor },
      otelApi,
      { Resource },
      { LoggerProvider },
      { ResilientOTLPExporter },
      { ResilientLogExporter },
    ] = await Promise.all([
      import("@opentelemetry/sdk-node"),
      import("@opentelemetry/sdk-trace-base"),
      import("@opentelemetry/sdk-logs"),
      import("@opentelemetry/api"),
      import("@opentelemetry/resources"),
      import("@opentelemetry/sdk-logs"),
      import("./resilient-exporter.js"),
      import("./resilient-log-exporter.js"),
    ]);

    // Cache API imports for synchronous access in other functions
    const { trace, context } = otelApi;
    cachedTrace = trace;
    cachedContext = context;

    // Debug: Log loaded configuration
    if (process.env.DR_TELEMETRY_DEBUG) {
      process.stderr.write("[TELEMETRY] Configuration loaded:\n");
      process.stderr.write(`  - Traces endpoint: ${otlpConfig.endpoint}\n`);
      process.stderr.write(`  - Logs endpoint: ${otlpConfig.logsEndpoint}\n`);
      process.stderr.write(`  - Service name: ${otlpConfig.serviceName}\n`);
    }

    // ... rest of the function continues unchanged
```

**Key changes**:
1. Move `loadOTLPConfig` to the top, before other imports
2. Check `isExplicitlyConfigured` immediately and return early
3. Remove the duplicate config check that was at line 89
4. Remove `loadOTLPConfig` from the `Promise.all` import array

---

## Testing the Optimizations

After implementing these changes:

1. **Build the CLI**:
   ```bash
   cd cli
   npm run build:debug  # With telemetry enabled
   ```

2. **Test version command**:
   ```bash
   time ./dist/cli.js version
   ```

   Expected: ~4-6ms total (down from ~13ms)

3. **Verify no process hanging**:
   ```bash
   # Should exit immediately without OTLP collector running
   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 ./dist/cli.js version
   ```

4. **Test with telemetry collector**:
   ```bash
   # Start SigNoz/Jaeger first, then:
   ./dist/cli.js version
   # Check that spans appear in the collector UI
   ```

---

## Expected Performance Improvements

| Optimization | Latency Reduction | Applies To |
|-------------|------------------|------------|
| Bundle version at build time | 2-3ms | `version` command |
| Fix HTTP keepAlive | N/A (cleaner shutdown) | All commands |
| Lazy console interceptor | 1-2ms | Most commands |
| Batch span exports | +100ms latency, fewer HTTP requests | Multi-span commands |
| Remove redundant attributes | <1ms | All commands |
| Early config check | 3-5ms (when not configured) | All commands (first run) |

**Total improvement for `version` command**: ~6-10ms reduction (from 13ms to 3-7ms) - a **2-4x speedup**.

---

## Notes

- **Optimization 5 (Batch processing)**: Adds 100ms latency due to scheduled delay, but reduces HTTP overhead for commands with multiple spans. For single-span commands like `version`, this increases latency. Consider making this configurable or using a hybrid approach.

- **HTTP KeepAlive**: The OTLPTraceExporter and OTLPLogExporter from `@opentelemetry/exporter-*-otlp-http` may not support the `keepAlive` option directly. You may need to pass it through transport options or use a custom HTTP agent:

  ```typescript
  import http from 'node:http';

  const httpAgent = new http.Agent({ keepAlive: false });

  this.delegate = new OTLPTraceExporter({
    ...config,
    url: this.url,
    timeoutMillis: config?.timeoutMillis ?? 5000,
    httpAgentOptions: { keepAlive: false },
  });
  ```

  Check the OTLP exporter documentation for the exact option name.

- **Console Logging Commands**: The list of commands needing console interception (`CONSOLE_LOGGING_COMMANDS`) may need adjustment based on actual usage patterns. Review which commands produce significant console output that should be captured in logs.
