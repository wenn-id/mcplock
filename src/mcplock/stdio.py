from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
from typing import Any, Sequence

SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
})
LATEST_PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
STDERR_LIMIT = 32 * 1024


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


class DiscoveryError(RuntimeError):
    def __init__(self, message, diagnostic=""):
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class Discovery:
    protocol_version: str
    server: dict[str, Any]
    tools: list[dict[str, Any]]


class _Client:
    def __init__(self, process, timeout):
        self.process = process
        self.timeout = timeout
        self.next_id = 1

    async def _send(self, message):
        data = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def _read(self):
        try:
            line = await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise DiscoveryError("MCP request timed out") from exc
        except ValueError as exc:
            raise DiscoveryError("MCP message exceeds 16 MiB") from exc
        if not line:
            raise DiscoveryError("MCP server exited before responding")
        if len(line) > MAX_MESSAGE_BYTES:
            raise DiscoveryError("MCP message exceeds 16 MiB")
        try:
            if not line.endswith(b"\n"):
                raise ValueError("MCP message is not newline-terminated")
            message = json.loads(
                line.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            preview = line[:160].decode("utf-8", errors="replace").rstrip()
            raise DiscoveryError(f"invalid MCP stdout: {preview}") from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise DiscoveryError("MCP message must be a JSON-RPC 2.0 object")
        return message

    async def notify(self, method):
        await self._send({"jsonrpc": "2.0", "method": method})

    async def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        await self._send(request)
        while True:
            response = await self._read()
            if "method" in response and "id" not in response:
                continue
            if "method" in response and "id" in response:
                await self._send({
                    "jsonrpc": "2.0",
                    "id": response["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                })
                continue
            if response.get("id") != request_id:
                raise DiscoveryError("MCP response used an unexpected request id")
            if "error" in response:
                error = response["error"]
                text = error.get("message", "unknown error") if isinstance(
                    error, dict
                ) else str(error)
                raise DiscoveryError(f"MCP {method} failed: {text}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise DiscoveryError(f"MCP {method} returned a non-object result")
            return result


async def _drain_stderr(stream, tail):
    while chunk := await stream.read(4096):
        tail.extend(chunk)
        if len(tail) > STDERR_LIMIT:
            del tail[:-STDERR_LIMIT]


async def _shutdown(process):
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
        return
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=0.5)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def discover(command: Sequence[str], timeout: float = 30.0) -> Discovery:
    if not command:
        raise DiscoveryError("server command is required")
    if timeout <= 0:
        raise DiscoveryError("timeout must be greater than zero")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_MESSAGE_BYTES + 1,
        )
    except OSError as exc:
        raise DiscoveryError(f"could not launch MCP server: {exc}") from exc

    stderr_tail = bytearray()
    stderr_task = asyncio.create_task(
        _drain_stderr(process.stderr, stderr_tail)
    )
    client = _Client(process, timeout)
    failure = None
    discovered = None
    try:
        initialized = await client.request("initialize", {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcplock", "version": "0.1.0"},
        })
        protocol_version = initialized.get("protocolVersion")
        if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise DiscoveryError(
                f"unsupported MCP protocol version: {protocol_version}"
            )
        capabilities = initialized.get("capabilities")
        if not isinstance(capabilities, dict) or "tools" not in capabilities:
            raise DiscoveryError(
                "MCP server did not advertise the tools capability"
            )
        server = initialized.get("serverInfo")
        if not isinstance(server, dict):
            raise DiscoveryError("MCP initialize result omitted serverInfo")
        await client.notify("notifications/initialized")

        tools = []
        cursor = None
        seen_cursors = set()
        while True:
            params = {"cursor": cursor} if cursor is not None else {}
            page = await client.request("tools/list", params)
            page_tools = page.get("tools")
            if not isinstance(page_tools, list) or not all(
                isinstance(item, dict) for item in page_tools
            ):
                raise DiscoveryError(
                    "MCP tools/list returned an invalid tools array"
                )
            tools.extend(page_tools)
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise DiscoveryError(
                    "MCP tools/list returned an invalid nextCursor"
                )
            if next_cursor in seen_cursors:
                raise DiscoveryError(
                    f"MCP pagination cursor repeated: {next_cursor}"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        names = []
        for item in tools:
            name = item.get("name")
            if not isinstance(name, str) or not name:
                raise DiscoveryError(
                    "MCP tools/list returned a tool without a valid name"
                )
            names.append(name)
        if len(names) != len(set(names)):
            raise DiscoveryError(
                "MCP tools/list returned duplicate tool names"
            )
        discovered = Discovery(protocol_version, server, tools)
    except BaseException as exc:
        failure = exc
    finally:
        await _shutdown(process)
        await stderr_task

    diagnostic = stderr_tail.decode("utf-8", errors="replace").rstrip()
    if failure is not None:
        if isinstance(failure, DiscoveryError):
            raise DiscoveryError(str(failure), diagnostic) from failure
        raise failure
    assert discovered is not None
    return discovered
