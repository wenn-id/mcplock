from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

LOCK_VERSION = 1
Json = dict[str, Any] | list[Any] | str | int | float | bool | None
_SCALAR = (str, int, float, bool, type(None))
_ALL_TYPES = frozenset({
    "array", "boolean", "integer", "null", "number", "object", "string",
})
_KNOWN_SCHEMA_KEYS = {
    "type", "enum", "properties", "required", "additionalProperties",
}
_ORDER = {"breaking": 0, "warning": 1, "info": 2}
_MISSING = object()


class ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Change:
    severity: str
    path: str
    message: str


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


def _reject_constant(value):
    raise ValueError(f"invalid JSON number: {value}")


def load_lock(path):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ContractError(
            f"lockfile not found: {path}; run mcplock update first"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ContractError(f"invalid lockfile JSON: {path}") from exc
    try:
        lock = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid lockfile JSON: {path}") from exc
    if not isinstance(lock, dict) or lock.get("lockVersion") != LOCK_VERSION:
        raise ContractError(f"unsupported lockfile version in {path}")
    required = {"protocolVersion", "server", "stats", "tools"}
    if not required.issubset(lock):
        raise ContractError(f"lockfile is missing required fields: {path}")
    try:
        rebuilt = build_lock(lock["protocolVersion"], lock["server"], lock["tools"])
    except (KeyError, TypeError, ContractError) as exc:
        raise ContractError(f"invalid lockfile structure: {path}") from exc
    if rebuilt != lock:
        raise ContractError(f"lockfile is not canonical or has invalid stats: {path}")
    return lock


def write_lock(path, lock):
    if not path.parent.is_dir():
        raise OSError(f"lockfile directory does not exist: {path.parent}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialize_lock(lock))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _types(schema):
    value = schema.get("type", _MISSING)
    if value is _MISSING:
        return _ALL_TYPES
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    return None


def _strings(value):
    return set(value) if isinstance(value, list) and all(
        isinstance(item, str) for item in value
    ) else None


def _enums(value):
    return {_json_key(item): item for item in value} if isinstance(value, list) else None


def _raw(mapping, key):
    return mapping[key] if key in mapping else _MISSING


def _emit(changes, severity, path, message):
    changes.append(Change(severity, path, message))


def _compare_schema(baseline, current, path, changes):
    if baseline == current:
        return

    old_types, new_types = _types(baseline), _types(current)
    if old_types is not None and new_types is not None and old_types != new_types:
        if not old_types.issubset(new_types):
            _emit(
                changes,
                "breaking",
                f"{path}.type",
                "accepted JSON types narrowed",
            )
        elif old_types < new_types:
            _emit(
                changes,
                "info",
                f"{path}.type",
                "accepted JSON types broadened",
            )
    elif _raw(baseline, "type") != _raw(current, "type"):
        _emit(changes, "warning", f"{path}.type", "type expression changed")

    old_required = _strings(baseline.get("required", []))
    new_required = _strings(current.get("required", []))
    if old_required is not None and new_required is not None:
        for name in sorted(new_required - old_required):
            _emit(
                changes,
                "breaking",
                f"{path}.required.{name}",
                "input became required",
            )
        for name in sorted(old_required - new_required):
            _emit(
                changes,
                "info",
                f"{path}.required.{name}",
                "input became optional",
            )
    elif _raw(baseline, "required") != _raw(current, "required"):
        _emit(changes, "warning", f"{path}.required", "required expression changed")
        new_required = set()

    old_properties = baseline.get("properties", {})
    new_properties = current.get("properties", {})
    if isinstance(old_properties, dict) and isinstance(new_properties, dict):
        for name in sorted(old_properties.keys() - new_properties.keys()):
            _emit(
                changes,
                "breaking",
                f"{path}.properties.{name}",
                "input property removed",
            )
        for name in sorted(new_properties.keys() - old_properties.keys()):
            if new_required is None or name not in new_required:
                _emit(
                    changes,
                    "info",
                    f"{path}.properties.{name}",
                    "optional input property added",
                )
        for name in sorted(old_properties.keys() & new_properties.keys()):
            old_child, new_child = old_properties[name], new_properties[name]
            child_path = f"{path}.properties.{name}"
            if isinstance(old_child, dict) and isinstance(new_child, dict):
                _compare_schema(old_child, new_child, child_path, changes)
            elif old_child != new_child:
                _emit(changes, "warning", child_path, "property schema changed")
    elif old_properties != new_properties:
        _emit(changes, "warning", f"{path}.properties", "properties expression changed")

    old_enum_raw = _raw(baseline, "enum")
    new_enum_raw = _raw(current, "enum")
    old_enum, new_enum = _enums(old_enum_raw), _enums(new_enum_raw)
    if old_enum is not None and new_enum is not None:
        for key in sorted(old_enum.keys() - new_enum.keys()):
            _emit(
                changes,
                "breaking",
                f"{path}.enum",
                f"enum value removed: {_json_key(old_enum[key])}",
            )
        for key in sorted(new_enum.keys() - old_enum.keys()):
            _emit(
                changes,
                "info",
                f"{path}.enum",
                f"enum value added: {_json_key(new_enum[key])}",
            )
    elif old_enum_raw != new_enum_raw:
        _emit(changes, "warning", f"{path}.enum", "enum expression changed")

    old_extra = baseline.get("additionalProperties", True)
    new_extra = current.get("additionalProperties", True)
    if old_extra is not False and new_extra is False:
        _emit(
            changes,
            "breaking",
            f"{path}.additionalProperties",
            "additional properties forbidden",
        )
    elif old_extra is False and new_extra is not False:
        _emit(
            changes,
            "info",
            f"{path}.additionalProperties",
            "additional properties allowed",
        )
    elif old_extra != new_extra:
        _emit(
            changes,
            "warning",
            f"{path}.additionalProperties",
            "additionalProperties schema changed",
        )

    for key in sorted((baseline.keys() | current.keys()) - _KNOWN_SCHEMA_KEYS):
        if _raw(baseline, key) != _raw(current, key):
            _emit(changes, "warning", f"{path}.{key}", "schema keyword changed")


def compare_locks(baseline, current):
    changes = []
    if baseline["protocolVersion"] != current["protocolVersion"]:
        _emit(changes, "warning", "protocolVersion", "protocol version changed")
    for key in ("name", "version"):
        if baseline["server"][key] != current["server"][key]:
            _emit(changes, "warning", f"server.{key}", "server identity changed")

    old_tools = {item["name"]: item for item in baseline["tools"]}
    new_tools = {item["name"]: item for item in current["tools"]}
    for name in sorted(old_tools.keys() - new_tools.keys()):
        _emit(changes, "breaking", f"tools.{name}", "tool removed")
    for name in sorted(new_tools.keys() - old_tools.keys()):
        _emit(changes, "info", f"tools.{name}", "tool added")
    for name in sorted(old_tools.keys() & new_tools.keys()):
        old_tool, new_tool = old_tools[name], new_tools[name]
        _compare_schema(
            old_tool["inputSchema"],
            new_tool["inputSchema"],
            f"tools.{name}.inputSchema",
            changes,
        )
        metadata = (old_tool.keys() | new_tool.keys()) - {"name", "inputSchema"}
        for key in sorted(metadata):
            if _raw(old_tool, key) != _raw(new_tool, key):
                _emit(
                    changes,
                    "warning",
                    f"tools.{name}.{key}",
                    "tool metadata changed",
                )

    old_bytes = baseline["stats"]["definitionBytes"]
    new_bytes = current["stats"]["definitionBytes"]
    growth = new_bytes - old_bytes
    if growth > 0 and (growth >= 1024 or (old_bytes > 0 and growth / old_bytes >= 0.10)):
        percent = round(growth / old_bytes * 100) if old_bytes else 0
        _emit(
            changes,
            "warning",
            "stats.definitionBytes",
            f"context grew {old_bytes} -> {new_bytes} bytes (+{percent}%)",
        )
    elif growth < 0:
        _emit(
            changes,
            "info",
            "stats.definitionBytes",
            f"context shrank {old_bytes} -> {new_bytes} bytes",
        )

    return sorted(
        changes,
        key=lambda item: (_ORDER[item.severity], item.path, item.message),
    )
