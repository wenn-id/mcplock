import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcplock.stdio import (
    Discovery,
    DiscoveryError,
    SUPPORTED_PROTOCOL_VERSIONS,
    discover,
)

FAKE_SERVER = Path(__file__).with_name("fake_server.py")


def command(scenario):
    return [sys.executable, str(FAKE_SERVER), "--scenario", scenario]


def raw_command(payload):
    script = (
        "import sys;"
        f"sys.stdout.buffer.write({payload!r});"
        "sys.stdout.buffer.flush();"
        "sys.stdout.close()"
    )
    return [sys.executable, "-c", script]


class DiscoveryTests(unittest.TestCase):
    def assert_discovery_error(self, scenario, text, timeout=2.0):
        with self.assertRaises(DiscoveryError) as caught:
            asyncio.run(discover(command(scenario), timeout=timeout))
        self.assertIn(text, str(caught.exception))
        return caught.exception

    def test_supported_versions_are_exact(self):
        self.assertEqual(SUPPORTED_PROTOCOL_VERSIONS, {
            "2025-11-25",
            "2025-06-18",
            "2025-03-26",
            "2024-11-05",
        })

    def test_discovers_one_tool(self):
        result = asyncio.run(discover(command("baseline"), timeout=2.0))
        self.assertIsInstance(result, Discovery)
        self.assertEqual(result.protocol_version, "2025-11-25")
        self.assertEqual(result.server, {"name": "fixture", "version": "1.0.0"})
        self.assertEqual([item["name"] for item in result.tools], ["read_file"])

    def test_follows_every_page(self):
        result = asyncio.run(discover(command("pagination"), timeout=2.0))
        self.assertEqual([item["name"] for item in result.tools], ["alpha", "zeta"])

    def test_accepts_oldest_supported_revision(self):
        result = asyncio.run(discover(command("legacy"), timeout=2.0))
        self.assertEqual(result.protocol_version, "2024-11-05")

    def test_tool_count_triggers_discovery_error(self):
        with patch("mcplock.stdio.MAX_TOOLS", 3):
            self.assert_discovery_error(
                "too-many-tools",
                "exposed more than 3 tools",
            )

    def test_page_count_triggers_discovery_error(self):
        with patch("mcplock.stdio.MAX_PAGES", 3):
            self.assert_discovery_error(
                "too-many-pages",
                "paged more than 3 times",
            )

    def test_notifications_and_rejected_client_requests_continue(self):
        for scenario in ("notification", "client-request"):
            with self.subTest(scenario=scenario):
                result = asyncio.run(discover(command(scenario), timeout=2.0))
                self.assertEqual(
                    [item["name"] for item in result.tools],
                    ["read_file"],
                )

    def test_response_id_requires_an_exact_integer(self):
        self.assert_discovery_error(
            "boolean-id",
            "unexpected request id",
        )

    def test_protocol_failures_are_actionable(self):
        cases = {
            "duplicate": "duplicate tool names",
            "cycle": "cursor repeated",
            "invalid-tool": "valid name",
            "invalid-cursor": "invalid nextCursor",
            "rpc-error": "tools/list failed: fixture failure",
            "bad-jsonrpc": "JSON-RPC 2.0 object",
            "malformed": "invalid MCP stdout",
            "missing-tools": "tools capability",
            "unsupported": "unsupported MCP protocol version",
            "oversized": "exceeds 16 MiB",
        }
        for scenario, text in cases.items():
            with self.subTest(scenario=scenario):
                self.assert_discovery_error(scenario, text)
        for payload in (
            b'{"jsonrpc":"2.0","id":1,"result":{}}',
            b'{"jsonrpc":"2.0","id":1,"result":NaN}\n',
            b'{"jsonrpc":"2.0","id":1,"result":Infinity}\n',
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    DiscoveryError,
                    "invalid MCP stdout",
                ):
                    asyncio.run(discover(raw_command(payload), timeout=2.0))

    def test_stderr_is_bounded_diagnostic_data(self):
        error = self.assert_discovery_error(
            "stderr-exit",
            "exited before responding",
        )
        self.assertEqual(
            error.diagnostic,
            "fixture launch failed: secret-free diagnostic",
        )
        self.assertLessEqual(len(error.diagnostic.encode("utf-8")), 32 * 1024)
        large = self.assert_discovery_error(
            "stderr-large",
            "exited before responding",
        )
        self.assertLessEqual(len(large.diagnostic.encode("utf-8")), 32 * 1024)
        self.assertTrue(large.diagnostic.endswith("TAIL"))
        invalid = self.assert_discovery_error(
            "stderr-invalid",
            "exited before responding",
        )
        self.assertLessEqual(len(invalid.diagnostic.encode("utf-8")), 32 * 1024)
        self.assertTrue(invalid.diagnostic.endswith("TAIL"))

    def test_timeout_returns_after_child_cleanup(self):
        loop = asyncio.new_event_loop()
        started = loop.time()
        try:
            with self.assertRaisesRegex(DiscoveryError, "timed out"):
                loop.run_until_complete(discover(command("hang"), timeout=0.1))
            self.assertLess(loop.time() - started, 2.0)
        finally:
            loop.close()

    def test_descendant_inherited_stderr_does_not_block_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(FAKE_SERVER.parent.parent / "src")
            pid_path = Path(directory) / "descendant.pid"
            env["MCPLOCK_DESCENDANT_PID_FILE"] = str(pid_path)
            code = (
                "import asyncio;"
                "from mcplock.stdio import discover;"
                f"result = asyncio.run(discover({command('stderr-descendant')!r}, "
                "timeout=1.0));"
                "print([item['name'] for item in result.tools])"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                env=env,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                try:
                    stdout, stderr = process.communicate(timeout=3.0)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    self.fail(
                        "discover() remained blocked after a descendant inherited stderr"
                    )
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "['read_file']")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                if pid_path.exists():
                    descendant_pid = int(pid_path.read_text(encoding="ascii"))
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(descendant_pid), "/T", "/F"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        try:
                            os.kill(descendant_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_request_timeout_is_one_total_deadline(self):
        async def run():
            return await asyncio.wait_for(
                discover(command("notification-stream"), timeout=1.0),
                timeout=5.0,
            )

        with self.assertRaisesRegex(DiscoveryError, "timed out"):
            asyncio.run(run())

    def test_arguments_are_validated_before_launch(self):
        with self.assertRaisesRegex(DiscoveryError, "command is required"):
            asyncio.run(discover([], timeout=1))
        with self.assertRaisesRegex(DiscoveryError, "greater than zero"):
            asyncio.run(discover(command("baseline"), timeout=0))

    def test_non_finite_timeouts_are_validated_before_launch(self):
        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                with patch(
                    "mcplock.stdio.asyncio.create_subprocess_exec",
                    side_effect=OSError("launch attempted"),
                ) as launch:
                    with self.assertRaisesRegex(
                        DiscoveryError,
                        "timeout must be greater than zero",
                    ):
                        asyncio.run(
                            discover(command("baseline"), timeout=timeout)
                        )
                    launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
