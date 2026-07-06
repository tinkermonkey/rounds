"""Transport layer for invoking Claude Code on remote agent nodes.

Defines the AgentNodeClient protocol used by agent-node diagnosis adapters
to reach remote nodes, and its Phase 1 concrete implementation, which
invokes the `claude` CLI over SSH.
"""

import asyncio
import json
import logging
import shlex
import subprocess
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AgentNodeClient(Protocol):
    """Transport abstraction for invoking Claude Code on a remote agent node."""

    async def invoke(self, mcp_key: str, workspace: str, prompt: str) -> dict[str, Any]:
        """Invoke Claude Code on the agent node identified by mcp_key.

        Args:
            mcp_key: Identifier for the target agent node.
            workspace: Workspace directory on the remote node to run in.
            prompt: Investigation prompt to pass to Claude Code.

        Returns:
            Parsed JSON dict from the Claude Code response.
        """
        ...


class SshAgentNodeClient:
    """Phase 1 AgentNodeClient transport: invokes `claude` over SSH.

    Resolves mcp_key to an SSH host via a simple lookup table passed at
    construction. Host resolution is isolated in `_resolve_host` so it can
    later be swapped for an MCP- or RemoteTrigger-based mechanism without
    changing callers of `invoke`.
    """

    def __init__(self, host_map: dict[str, str], timeout_seconds: int = 120):
        """Initialize the SSH transport.

        Args:
            host_map: Mapping from mcp_key to SSH-reachable hostname.
            timeout_seconds: Timeout in seconds for the remote SSH command.
        """
        self._host_map = host_map
        self._timeout_seconds = timeout_seconds

    async def invoke(self, mcp_key: str, workspace: str, prompt: str) -> dict[str, Any]:
        """Invoke `claude` on the remote host over SSH and return parsed JSON output.

        Raises:
            ValueError: If mcp_key cannot be resolved to a host, or the
                response contains no parseable JSON dict.
            TimeoutError: If the SSH command times out.
            RuntimeError: If the SSH command returns a non-zero exit code.
        """
        host = self._resolve_host(mcp_key)

        # ssh concatenates the arguments after the host with spaces and hands them
        # to the remote login shell for re-parsing, so the prompt and workspace
        # must be shell-quoted here to survive that re-parsing intact.
        remote_command = " ".join(
            shlex.quote(part)
            for part in (
                "claude",
                "-p",
                prompt,
                "--output-format",
                "json",
                "--cwd",
                workspace,
            )
        )
        # `--` stops ssh from interpreting `host` as an option flag (e.g. a
        # misconfigured value like "-oProxyCommand=..." would otherwise be
        # parsed as an ssh option rather than the target host).
        cmd = ["ssh", "--", host, remote_command]

        def _run_ssh() -> str:
            """Synchronous wrapper for the remote subprocess call."""
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                )
                if result.returncode != 0:
                    error_output = result.stderr or result.stdout
                    raise RuntimeError(
                        f"Agent node SSH invocation on {host!r} failed: {error_output}"
                    )
                return result.stdout.strip()
            except subprocess.TimeoutExpired:
                raise TimeoutError(
                    f"Agent node SSH invocation on {host!r} timed out "
                    f"after {self._timeout_seconds} seconds"
                )

        try:
            output = await asyncio.to_thread(_run_ssh)
        except TimeoutError as e:
            logger.error(f"Agent node SSH timeout: {e}", exc_info=True)
            raise TimeoutError(str(e)) from e
        except RuntimeError as e:
            logger.error(f"Agent node SSH error: {e}", exc_info=True)
            raise RuntimeError(str(e)) from e

        # Claude Code --output-format json wraps the response in an envelope:
        # {"type": "result", "subtype": "success", "result": "<text>", ...}
        # Unwrap that first; the inner text is expected to be the diagnosis JSON.
        text_to_parse = output
        try:
            outer: dict[str, Any] = json.loads(output)
            if isinstance(outer, dict) and "type" in outer and "result" in outer:
                if outer.get("is_error"):
                    raise RuntimeError(
                        f"Agent node {host!r} returned error: {outer.get('result', 'unknown')}"
                    )
                text_to_parse = outer.get("result", "") or output
        except json.JSONDecodeError as e:
            logger.debug(
                f"Agent node {host!r} output is not an envelope JSON object, "
                f"treating as raw output: {e}"
            )

        try:
            parsed: dict[str, Any] = json.loads(text_to_parse)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except json.JSONDecodeError as e:
            logger.debug(
                f"Agent node {host!r} output is not a bare JSON object, "
                f"falling back to embedded block search: {e}"
            )

        block = self._find_json_object(text_to_parse)
        if block is not None:
            return block

        raise ValueError(
            f"Agent node {host!r} returned no parseable JSON. "
            f"Output: {text_to_parse[:500]}"
        )

    @staticmethod
    def _find_json_object(text: str) -> dict[str, Any] | None:
        """Find the first complete JSON object embedded in prose, via brace-counting.

        Claude Code may respond with prose plus an embedded JSON block instead of
        pure JSON, so a naive `json.loads` on the full text is not sufficient.
        """
        json_buffer: list[str] = []
        in_json = False
        brace_count = 0

        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("{"):
                in_json = True
                brace_count = 0
                json_buffer = []

            if in_json:
                json_buffer.append(line)
                brace_count += line.count("{") - line.count("}")

                if brace_count == 0:
                    json_str = "\n".join(json_buffer)
                    try:
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict) and parsed:
                            return parsed
                    except json.JSONDecodeError:
                        logger.warning(
                            f"Failed to parse JSON block from agent node output. "
                            f"Content: {json_str[:200]}"
                        )
                    json_buffer = []
                    in_json = False

        return None

    def _resolve_host(self, mcp_key: str) -> str:
        """Resolve mcp_key to an SSH host using the configured lookup table.

        Raises:
            ValueError: If mcp_key has no corresponding host.
        """
        host = self._host_map.get(mcp_key)
        if not host:
            raise ValueError(f"No SSH host configured for agent node mcp_key={mcp_key!r}")
        return host
