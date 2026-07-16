# MCPLock

> Catch breaking MCP tool changes before your agents do.

MCPLock creates a Git-friendly lockfile for one stdio MCP server's `tools/list`
contract. Run it again in CI to catch removed tools, newly required inputs,
narrowed types, removed enum values, and forbidden additional properties.

- No model or API key
- No service, daemon, database, or telemetry
- No runtime dependencies
- Deterministic JSON suitable for code review

## One-minute offline demo

~~~console
python -m pip install -e . --no-deps
mcplock update -- python tests/fake_server.py --scenario baseline
mcplock check -- python tests/fake_server.py --scenario breaking
~~~

The second command exits `1` and identifies the new required input. The first
command writes `mcp.lock.json`; commit that file beside your MCP server.

## Use it with your server

~~~console
mcplock update -- python my_server.py
git add mcp.lock.json
mcplock check -- python my_server.py
~~~

Use `--lock path/to/name.lock.json` for multiple servers and `--timeout 60`
for slow startup. MCPLock passes every token after `--` directly to the child
process without invoking a shell.

## CI

~~~yaml
- name: Check MCP tool contract
  run: mcplock check -- python my_server.py
~~~

Exit codes are `0` for compatible or warning-only changes, `1` for certain
breaking changes, `2` for usage/lockfile/launch/protocol failures, and `130`
for user interruption.

## What counts as breaking

MCPLock fails for a removed tool, newly required input, removed input
property, narrowed JSON type, removed enum value, or a change from allowed
additional properties to forbidden. Every other observed contract change is
shown for review without claiming complete JSON Schema analysis.

## Scope

Version 0.1 supports stdio MCP revisions `2025-11-25`, `2025-06-18`,
`2025-03-26`, and `2024-11-05`. It does not call tools, connect over HTTP,
scan for vulnerabilities, use models, or upload data.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). MCPLock is MIT licensed.
