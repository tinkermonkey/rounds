"""Unit tests for the agent node SSH transport client."""

import shlex
import subprocess
from unittest.mock import patch

import pytest

from rounds.adapters.diagnosis._client import SshAgentNodeClient


def _completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class TestSshAgentNodeClientInvoke:
    """Tests for SshAgentNodeClient.invoke()."""

    @pytest.mark.asyncio
    async def test_invoke_constructs_ssh_command_and_returns_parsed_json(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})
        envelope = (
            '{"type": "result", "subtype": "success", '
            '"result": "{\\"root_cause\\": \\"pool exhausted\\"}"}'
        )

        with patch("subprocess.run", return_value=_completed_process(envelope)) as mock_run:
            result = await client.invoke(
                mcp_key="node1", workspace="/workspace/target", prompt="diagnose this"
            )

        assert result == {"root_cause": "pool exhausted"}
        called_cmd = mock_run.call_args.args[0]
        assert called_cmd == [
            "ssh",
            "--",
            "worker-host",
            "claude -p 'diagnose this' --output-format json --cwd /workspace/target",
        ]

    @pytest.mark.asyncio
    async def test_invoke_shell_quotes_prompt_with_special_characters(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})
        tricky_prompt = "diagnose `whoami`; echo $HOME && rm -rf /tmp; 'quoted' \"double\""

        with patch(
            "subprocess.run", return_value=_completed_process('{"summary": "ok"}')
        ) as mock_run:
            await client.invoke(
                mcp_key="node1", workspace="/workspace/target", prompt=tricky_prompt
            )

        remote_command = mock_run.call_args.args[0][3]
        # The prompt must appear as a single shell-quoted token so the remote
        # shell does not execute embedded metacharacters as separate commands.
        assert shlex.split(remote_command)[2] == tricky_prompt

    @pytest.mark.asyncio
    async def test_invoke_passes_configured_timeout_to_subprocess(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"}, timeout_seconds=5)

        with patch(
            "subprocess.run", return_value=_completed_process('{"summary": "ok"}')
        ) as mock_run:
            await client.invoke(
                mcp_key="node1", workspace="/workspace/target", prompt="diagnose"
            )

        assert mock_run.call_args.kwargs["timeout"] == 5

    @pytest.mark.asyncio
    async def test_invoke_parses_raw_json_without_envelope(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})
        raw_output = '{"summary": "flows through gateway"}'

        with patch("subprocess.run", return_value=_completed_process(raw_output)):
            result = await client.invoke(
                mcp_key="node1", workspace="/workspace/target", prompt="explain"
            )

        assert result == {"summary": "flows through gateway"}

    @pytest.mark.asyncio
    async def test_invoke_extracts_json_block_embedded_in_prose(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})
        envelope = (
            '{"type": "result", "subtype": "success", "result": '
            '"Here is my analysis:\\n\\n{\\"summary\\": \\"partial json\\"}\\n\\nDone."}'
        )

        with patch("subprocess.run", return_value=_completed_process(envelope)):
            result = await client.invoke(
                mcp_key="node1", workspace="/workspace/target", prompt="explain"
            )

        assert result == {"summary": "partial json"}

    @pytest.mark.asyncio
    async def test_unresolvable_mcp_key_raises_value_error(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})

        with pytest.raises(ValueError, match="unknown-node"):
            await client.invoke(
                mcp_key="unknown-node", workspace="/workspace/target", prompt="diagnose"
            )

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_raises_runtime_error(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})

        with patch(
            "subprocess.run",
            return_value=_completed_process("", returncode=1),
        ):
            with pytest.raises(RuntimeError):
                await client.invoke(
                    mcp_key="node1", workspace="/workspace/target", prompt="diagnose"
                )

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=120),
        ):
            with pytest.raises(TimeoutError):
                await client.invoke(
                    mcp_key="node1", workspace="/workspace/target", prompt="diagnose"
                )

    @pytest.mark.asyncio
    async def test_malformed_json_raises_value_error(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})

        with patch("subprocess.run", return_value=_completed_process("not json at all")):
            with pytest.raises(ValueError, match="no parseable JSON"):
                await client.invoke(
                    mcp_key="node1", workspace="/workspace/target", prompt="diagnose"
                )

    @pytest.mark.asyncio
    async def test_envelope_error_response_raises_runtime_error(self) -> None:
        client = SshAgentNodeClient(host_map={"node1": "worker-host"})
        envelope = '{"type": "result", "is_error": true, "result": "auth failed"}'

        with patch("subprocess.run", return_value=_completed_process(envelope)):
            with pytest.raises(RuntimeError, match="auth failed"):
                await client.invoke(
                    mcp_key="node1", workspace="/workspace/target", prompt="diagnose"
                )
