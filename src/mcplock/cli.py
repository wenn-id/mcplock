from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import sys

from .contract import (
    ContractError,
    build_lock,
    compare_locks,
    load_lock,
    write_lock,
)
from . import __version__
from .stdio import DiscoveryError, SUPPORTED_PROTOCOL_VERSIONS, discover


def positive_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def parser():
    result = argparse.ArgumentParser(
        prog="mcplock",
        description="Catch breaking MCP tool changes before your agents do.",
    )
    result.add_argument(
        "--version", action="version", version=f"mcplock {__version__}"
    )
    commands = result.add_subparsers(dest="action", required=True)
    for action in ("update", "check"):
        command = commands.add_parser(action)
        command.add_argument("--lock", type=Path, default=Path("mcp.lock.json"))
        command.add_argument("--timeout", type=positive_timeout, default=30.0)
        command.add_argument("--json", action="store_true")
        command.add_argument("server_command", nargs=argparse.REMAINDER)
    return result


def _emit_json_check(lock, changes):
    counts = {
        severity: sum(item.severity == severity for item in changes)
        for severity in ("breaking", "warning", "info")
    }
    document = {
        "lockVersion": lock["lockVersion"],
        "protocolVersion": lock["protocolVersion"],
        "server": lock["server"],
        "summary": counts,
        "changes": [
            {
                "severity": item.severity,
                "path": item.path,
                "message": item.message,
            }
            for item in changes
        ],
    }
    return json.dumps(document, sort_keys=True) + "\n"


def _emit_json_update(lock, path):
    return (
        json.dumps(
            {
                "lock": str(path),
                "lockVersion": lock["lockVersion"],
                "stats": lock["stats"],
            },
            sort_keys=True,
        )
        + "\n"
    )


def render_changes(changes):
    if not changes:
        return "MCPLock: compatible\n"
    counts = {
        severity: sum(item.severity == severity for item in changes)
        for severity in ("breaking", "warning", "info")
    }
    summary = ", ".join(
        f"{count} {severity}"
        for severity, count in counts.items()
        if count
    )
    lines = [f"MCPLock: {summary} change(s)", ""]
    for item in changes:
        lines.extend([
            f"{item.severity.upper():8}  {item.path}",
            f"          {item.message}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    args = parser().parse_args(argv)
    command = list(args.server_command)
    if not command or command.pop(0) != "--" or not command:
        print("MCPLock error: server command is required after --", file=sys.stderr)
        return 2
    try:
        discovery = discover(command, timeout=args.timeout)
        try:
            result = asyncio.run(discovery)
        finally:
            discovery.close()
        current = build_lock(result.protocol_version, result.server, result.tools)
        if args.action == "update":
            write_lock(args.lock, current)
            if args.json:
                print(_emit_json_update(current, args.lock), end="")
            else:
                print(
                    f"MCPLock: wrote {args.lock} "
                    f"({current['stats']['toolCount']} tools)"
                )
            return 0
        baseline = load_lock(args.lock)
        if baseline["protocolVersion"] not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ContractError(
                "unsupported lockfile protocol version: "
                f"{baseline['protocolVersion']}"
            )
        changes = compare_locks(baseline, current)
        if args.json:
            print(_emit_json_check(current, changes), end="")
        else:
            print(render_changes(changes), end="")
        return 1 if any(item.severity == "breaking" for item in changes) else 0
    except KeyboardInterrupt:
        return 130
    except (ContractError, DiscoveryError, OSError) as exc:
        print(f"MCPLock error: {exc}", file=sys.stderr)
        diagnostic = getattr(exc, "diagnostic", "")
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        return 2
