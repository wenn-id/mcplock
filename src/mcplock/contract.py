from __future__ import annotations

import json
import math
from typing import Any

LOCK_VERSION = 1
Json = dict[str, Any] | list[Any] | str | int | float | bool | None
_SCALAR = (str, int, float, bool, type(None))


class ContractError(ValueError):
    pass


def _json_key(value: Json) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _check_finite(value: Json) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("JSON numbers must be finite")
    if isinstance(value, dict):
        for child in value.values():
            _check_finite(child)
    elif isinstance(value, list):
        for child in value:
            _check_finite(child)


def canonicalize(value: Json, parent_key: str | None = None) -> Json:
    _check_finite(value)
    if isinstance(value, dict):
        return {key: canonicalize(value[key], key) for key in sorted(value)}
    if isinstance(value, list):
        items = [canonicalize(item) for item in value]
        if parent_key in {"required", "type"} and all(
            isinstance(item, str) for item in items
        ):
            return sorted(items)
        if parent_key == "enum" and all(isinstance(item, _SCALAR) for item in items):
            return sorted(items, key=_json_key)
        return items
    return value


def build_lock(
    protocol_version: str,
    server: dict[str, Any],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(protocol_version, str) or not protocol_version:
        raise ContractError("protocol version must be a non-empty string")
    if not isinstance(server, dict):
        raise ContractError("server info must be an object")
    name, version = server.get("name"), server.get("version")
    if not isinstance(name, str) or not name:
        raise ContractError("server name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ContractError("server version must be a non-empty string")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise ContractError("each tool must be an object")
        tool_name = tool.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            raise ContractError("tool name must be a non-empty string")
        if tool_name in seen:
            raise ContractError(f"duplicate tool name: {tool_name}")
        if not isinstance(tool.get("inputSchema"), dict):
            raise ContractError(f"tool {tool_name!r} requires an inputSchema object")
        if "outputSchema" in tool and not isinstance(tool["outputSchema"], dict):
            raise ContractError(f"tool {tool_name!r} outputSchema must be an object")
        seen.add(tool_name)
        item = canonicalize(tool)
        assert isinstance(item, dict)
        normalized.append(item)

    normalized.sort(key=lambda item: item["name"])
    compact = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "lockVersion": LOCK_VERSION,
        "protocolVersion": protocol_version,
        "server": {"name": name, "version": version},
        "stats": {"definitionBytes": len(compact), "toolCount": len(normalized)},
        "tools": normalized,
    }


def serialize_lock(lock: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            canonicalize(lock),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
