from copy import deepcopy
import json
import math
import unittest

from mcplock.contract import ContractError, build_lock, canonicalize, serialize_lock
from mcplock.contract import Change, compare_locks


class ContractArtifactTests(unittest.TestCase):
    def test_canonicalize_sorts_only_semantic_sets(self):
        value = {
            "z": 1,
            "required": ["beta", "alpha"],
            "type": ["string", "null"],
            "enum": ["z", "a", None],
            "oneOf": [{"type": "string"}, {"type": "integer"}],
        }
        result = canonicalize(value)
        self.assertEqual(list(result), ["enum", "oneOf", "required", "type", "z"])
        self.assertEqual(result["required"], ["alpha", "beta"])
        self.assertEqual(result["type"], ["null", "string"])
        self.assertEqual(result["enum"], ["a", "z", None])
        self.assertEqual(
            result["oneOf"],
            [{"type": "string"}, {"type": "integer"}],
        )

    def test_build_lock_sorts_tools_and_computes_stats(self):
        tools = [
            {"name": "zeta", "inputSchema": {"type": "object"}},
            {
                "name": "alpha",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ]
        lock = build_lock(
            "2025-11-25",
            {"name": "fixture", "version": "1.0.0", "title": "ignored"},
            tools,
        )
        self.assertEqual(lock["lockVersion"], 1)
        self.assertEqual(set(lock), {
            "lockVersion", "protocolVersion", "server", "stats", "tools",
        })
        self.assertEqual(lock["server"], {"name": "fixture", "version": "1.0.0"})
        self.assertEqual([item["name"] for item in lock["tools"]], ["alpha", "zeta"])
        compact = json.dumps(
            lock["tools"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(lock["stats"], {
            "definitionBytes": len(compact),
            "toolCount": 2,
        })

    def test_serialization_is_byte_identical_with_one_final_newline(self):
        lock = build_lock(
            "2025-11-25",
            {"name": "fixture", "version": "1.0.0"},
            [{"name": "ping", "inputSchema": {"type": "object"}}],
        )
        self.assertEqual(serialize_lock(lock), serialize_lock(lock))
        self.assertTrue(serialize_lock(lock).endswith(b"\n"))
        self.assertFalse(serialize_lock(lock).endswith(b"\n\n"))

    def test_invalid_tools_duplicate_names_and_nan_are_rejected(self):
        invalid = [
            {},
            {"name": "", "inputSchema": {}},
            {"name": "x"},
            {"name": "x", "inputSchema": None},
        ]
        for tool in invalid:
            with self.subTest(tool=tool), self.assertRaises(ContractError):
                build_lock(
                    "2025-11-25",
                    {"name": "fixture", "version": "1.0.0"},
                    [tool],
                )
        duplicate = {"name": "same", "inputSchema": {"type": "object"}}
        with self.assertRaisesRegex(ContractError, "duplicate tool name"):
            build_lock(
                "2025-11-25",
                {"name": "fixture", "version": "1.0.0"},
                [duplicate, duplicate],
            )
        with self.assertRaisesRegex(ContractError, "finite"):
            build_lock(
                "2025-11-25",
                {"name": "fixture", "version": "1.0.0"},
                [{"name": "bad", "inputSchema": {"maximum": math.inf}}],
            )


def make_tool():
    return {
        "name": "read_file",
        "description": "Read one file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "enum": ["a", "b"]},
                "encoding": {"type": ["string", "null"]},
            },
            "required": ["path"],
        },
    }


def make_lock(tool=None, server=None, protocol="2025-11-25"):
    return build_lock(
        protocol,
        server or {"name": "fixture", "version": "1.0.0"},
        [] if tool is False else [tool or make_tool()],
    )


class CompatibilityTests(unittest.TestCase):
    def assert_has(self, changes, severity, path, message):
        self.assertIn(
            (severity, path, message),
            [(c.severity, c.path, c.message) for c in changes],
        )

    def test_documented_breaking_changes(self):
        baseline = make_lock()
        cases = []

        cases.append((
            make_lock(False),
            "tools.read_file",
            "tool removed",
        ))

        required = make_tool()
        required["inputSchema"]["required"].append("encoding")
        cases.append((
            make_lock(required),
            "tools.read_file.inputSchema.required.encoding",
            "input became required",
        ))

        removed = make_tool()
        del removed["inputSchema"]["properties"]["encoding"]
        cases.append((
            make_lock(removed),
            "tools.read_file.inputSchema.properties.encoding",
            "input property removed",
        ))

        narrowed = make_tool()
        narrowed["inputSchema"]["properties"]["encoding"]["type"] = "string"
        cases.append((
            make_lock(narrowed),
            "tools.read_file.inputSchema.properties.encoding.type",
            "accepted JSON types narrowed",
        ))

        enum = make_tool()
        enum["inputSchema"]["properties"]["path"]["enum"] = ["a"]
        cases.append((
            make_lock(enum),
            "tools.read_file.inputSchema.properties.path.enum",
            'enum value removed: "b"',
        ))

        closed = make_tool()
        closed["inputSchema"]["additionalProperties"] = False
        cases.append((
            make_lock(closed),
            "tools.read_file.inputSchema.additionalProperties",
            "additional properties forbidden",
        ))

        for current, path, message in cases:
            with self.subTest(path=path):
                self.assert_has(
                    compare_locks(baseline, current),
                    "breaking",
                    path,
                    message,
                )

    def test_reverse_changes_are_informational(self):
        added_required = make_tool()
        added_required["inputSchema"]["required"].append("encoding")
        removed_property = make_tool()
        del removed_property["inputSchema"]["properties"]["encoding"]
        narrowed = make_tool()
        narrowed["inputSchema"]["properties"]["encoding"]["type"] = "string"
        smaller_enum = make_tool()
        smaller_enum["inputSchema"]["properties"]["path"]["enum"] = ["a"]
        closed = make_tool()
        closed["inputSchema"]["additionalProperties"] = False
        cases = [
            (make_lock(False), make_lock(), "tools.read_file", "tool added"),
            (
                make_lock(added_required),
                make_lock(),
                "tools.read_file.inputSchema.required.encoding",
                "input became optional",
            ),
            (
                make_lock(removed_property),
                make_lock(),
                "tools.read_file.inputSchema.properties.encoding",
                "optional input property added",
            ),
            (
                make_lock(narrowed),
                make_lock(),
                "tools.read_file.inputSchema.properties.encoding.type",
                "accepted JSON types broadened",
            ),
            (
                make_lock(smaller_enum),
                make_lock(),
                "tools.read_file.inputSchema.properties.path.enum",
                'enum value added: "b"',
            ),
            (
                make_lock(closed),
                make_lock(),
                "tools.read_file.inputSchema.additionalProperties",
                "additional properties allowed",
            ),
        ]
        for baseline, current, path, message in cases:
            with self.subTest(path=path):
                self.assert_has(
                    compare_locks(baseline, current),
                    "info",
                    path,
                    message,
                )

    def test_uncertain_metadata_schema_output_and_server_changes_warn(self):
        changed = make_tool()
        changed["description"] = "Changed guidance"
        changed["outputSchema"] = {"type": "object"}
        changed["inputSchema"]["properties"]["path"]["pattern"] = "^[a-z]+$"
        current = make_lock(
            changed,
            {"name": "renamed", "version": "2.0.0"},
            "2025-06-18",
        )
        paths = {
            item.path
            for item in compare_locks(make_lock(), current)
            if item.severity == "warning"
        }
        self.assertTrue({
            "server.name",
            "server.version",
            "protocolVersion",
            "tools.read_file.description",
            "tools.read_file.outputSchema",
            "tools.read_file.inputSchema.properties.path.pattern",
        }.issubset(paths))

    def test_context_growth_warns_and_order_is_stable(self):
        changed = make_tool()
        changed["description"] += "x" * 1200
        changes = compare_locks(make_lock(), make_lock(changed))
        self.assertTrue(any(
            item.severity == "warning" and item.path == "stats.definitionBytes"
            for item in changes
        ))
        order = {"breaking": 0, "warning": 1, "info": 2}
        self.assertEqual(
            changes,
            sorted(changes, key=lambda item: (
                order[item.severity], item.path, item.message
            )),
        )
        shrink = compare_locks(make_lock(changed), make_lock())
        self.assertTrue(any(
            item.severity == "info" and item.path == "stats.definitionBytes"
            for item in shrink
        ))

    def test_missing_and_explicit_null_keyword_are_not_silently_equal(self):
        changed = make_tool()
        changed["inputSchema"]["properties"]["path"]["format"] = None
        changes = compare_locks(make_lock(), make_lock(changed))
        self.assert_has(
            changes,
            "warning",
            "tools.read_file.inputSchema.properties.path.format",
            "schema keyword changed",
        )

    def test_unchanged_contract_has_no_changes(self):
        lock = make_lock()
        self.assertEqual(compare_locks(lock, deepcopy(lock)), [])


if __name__ == "__main__":
    unittest.main()
