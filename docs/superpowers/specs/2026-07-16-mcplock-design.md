# MCPLock Design

**Status:** Approved  
**Date:** 2026-07-16  
**Audience:** Developers who build and maintain AI agents and MCP servers

## Summary

MCPLock is a local, dependency-free Python CLI that catches breaking changes in Model Context Protocol tool contracts before agents encounter them. It launches one stdio MCP server, records its complete paginated `tools/list` response in a deterministic `mcp.lock.json`, and compares future responses against that lockfile in local development or CI.

The product promise is deliberately narrow:

> Catch breaking MCP tool changes before your agents do.

MCPLock requires no model, API key, account, network service, database, daemon, or GUI. Its first release targets the latest stable MCP protocol revision, `2025-11-25`, and the earlier public initialization-era revisions `2025-06-18`, `2025-03-26`, and `2024-11-05`. It does not implement the future `2026-07-28` stateless protocol draft.

## Motivation

MCP tools are runtime contracts presented directly to language models. A renamed tool, newly required input, narrowed type, or removed enum value can break an agent even when the MCP server still launches successfully. Interactive inspection can reveal the current tool list, but teams lack a small, Git-native artifact that detects contract drift automatically.

The official MCP specification defines:

- newline-delimited JSON-RPC messages for stdio transport;
- an initialization and capability-negotiation lifecycle;
- a paginated `tools/list` operation; and
- JSON Schema input and optional output contracts for each tool.

MCPLock turns those existing protocol surfaces into a familiar lockfile workflow rather than adding a new server, proxy, hosted platform, or framework integration.

Primary protocol references:

- [MCP 2025-11-25 specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Tools and `tools/list`](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

## Goals

1. Produce a deterministic, reviewable lockfile for one stdio MCP server.
2. Detect a small set of certain breaking input-contract changes without pretending to solve arbitrary JSON Schema compatibility.
3. Show every other contract change as an explicit review warning or informational change.
4. Work locally and in CI without network access or provider credentials.
5. Reach a useful result within one minute of installation.
6. Remain maintainable by one developer with no runtime dependencies.

## Non-goals for V1

- Streamable HTTP or deprecated HTTP+SSE transports
- The draft `2026-07-28` stateless handshake
- Runtime proxying or policy enforcement
- Tool invocation, fuzzing, or security scanning
- Hosted storage, dashboards, accounts, or telemetry
- Model-provider adapters
- Complete JSON Schema equivalence or satisfiability analysis
- Automatic migration of changed contracts
- Monitoring `notifications/tools/list_changed` after the initial snapshot
- Multiple servers in one lockfile

Multiple servers remain possible by giving each invocation a different `--lock` path.

## Command-line experience

### Update a contract

```console
mcplock update -- python my_server.py
```

MCPLock launches the command after `--`, initializes the server, reads all pages of `tools/list`, canonicalizes the result, and atomically writes `mcp.lock.json` in the current directory.

An alternate lockfile path and request timeout are available without configuration files:

```console
mcplock update --lock contracts/filesystem.lock.json --timeout 30 -- python my_server.py
```

### Check a contract

```console
mcplock check -- python my_server.py
```

MCPLock repeats discovery, compares the live contract with the lockfile, prints a compact grouped diff, and exits with a stable status code.

Example output:

```text
MCPLock: 1 breaking change, 1 warning

BREAKING  tools.read_file.inputSchema.required
          added required input: encoding

WARNING   context size
          4,112 -> 5,308 bytes (+29%)

Run `mcplock update -- ...` after reviewing and accepting these changes.
```

MCPLock never updates the lockfile during `check`.

### Exit codes

| Code | Meaning |
|---:|---|
| `0` | No certain breaking changes; warnings may still be present |
| `1` | One or more certain breaking changes |
| `2` | Usage, lockfile, launch, timeout, or protocol failure |
| `130` | Interrupted by the user |

## Lockfile

The default artifact is `mcp.lock.json`. It contains only deterministic contract data:

```json
{
  "lockVersion": 1,
  "protocolVersion": "2025-11-25",
  "server": {
    "name": "example-server",
    "version": "1.0.0"
  },
  "stats": {
    "definitionBytes": 412,
    "toolCount": 1
  },
  "tools": [
    {
      "description": "Read a UTF-8 text file",
      "inputSchema": {
        "properties": {
          "path": {
            "type": "string"
          }
        },
        "required": [
          "path"
        ],
        "type": "object"
      },
      "name": "read_file"
    }
  ]
}
```

The lockfile omits generation timestamps, launch commands, working directories, environment variables, stderr, and other machine-specific or sensitive values. Identical contracts produce byte-identical UTF-8 files with a trailing newline.

`definitionBytes` is the byte length of the compact canonical JSON representation of `tools`. It is a deterministic proxy for context growth, not a provider-specific token count.

## Architecture

MCPLock uses Python 3.11 or newer and the standard library only. Four internal units have narrow responsibilities.

### CLI

The CLI uses `argparse` to parse `update`, `check`, `--lock`, `--timeout`, and the server argument vector after `--`. It validates inputs, coordinates the workflow, renders diagnostics, and maps outcomes to exit codes. It does not invoke a shell.

### Stdio client

The client uses `asyncio.create_subprocess_exec` to launch the server. It:

1. sends `initialize` with protocol version `2025-11-25`, empty client capabilities, and MCPLock client identity;
2. accepts a response using `2025-11-25`, `2025-06-18`, `2025-03-26`, or `2024-11-05`;
3. verifies that the server advertises the `tools` capability;
4. sends `notifications/initialized`;
5. calls `tools/list` until `nextCursor` is absent;
6. rejects repeated cursors and duplicate tool names; and
7. closes stdin, waits briefly, then terminates or kills the child if necessary.

JSON-RPC request identifiers are monotonically increasing integers. Unsolicited notifications are ignored while awaiting a response. Server-to-client requests are rejected with JSON-RPC `-32601` because MCPLock advertises no client capabilities.

The four supported initialization-era revisions are explicit constants. MCPLock never silently claims support for an unknown protocol revision.

### Canonicalizer

The canonicalizer:

- sorts tool definitions by case-sensitive tool name;
- sorts keys in every JSON object;
- sorts `required` string arrays;
- sorts scalar `enum` values and scalar union `type` arrays by their canonical JSON representation; and
- preserves the order of every other array and all unfamiliar schema constructs.

This removes changes known to be semantically irrelevant while retaining anything uncertain for review.

### Diff engine and reporter

The diff engine compares the lockfile and live canonical representation, emits structured internal change records, and assigns each one to `breaking`, `warning`, or `info`. The reporter groups those records and prints stable, path-oriented text without a terminal-formatting dependency.

## Compatibility classification

V1 treats an input schema as a contract describing values an existing caller may send. Compatibility rules apply recursively to inline `properties` schemas.

### Certain breaking changes

- A tool is removed.
- A previously optional or absent input becomes required.
- An existing input property is removed.
- The set of accepted JSON types is narrowed.
- An existing enum value is removed.
- `additionalProperties` changes from allowed or unspecified to `false`.

A tool rename appears as one removal and one addition.

### Warnings requiring review

- Any input-schema change not proven breaking or compatible by the rules above
- Any output-schema change
- Any change to a title, description, annotation, icon, or execution metadata
- A server name or version change
- A context definition increase of at least 10 percent or 1,024 bytes
- A `$ref`, composition keyword, conditional schema, pattern, format, numeric bound, length bound, or unfamiliar keyword change

The context threshold uses logical OR: either a 10 percent increase or a 1,024-byte increase produces a warning. The percentage rule applies only when the previous definition size is greater than zero; any increase from zero is already represented by the added-tool information.

### Informational compatible changes

- A tool is added.
- An optional input property is added.
- A required input becomes optional.
- The set of accepted JSON types is broadened.
- An enum value is added.
- `additionalProperties` changes from `false` to allowed.
- Context definitions shrink without another contract change.

If the engine cannot establish compatibility confidently, it emits a warning. It never silently discards an observed difference.

## Failure handling and safety

- `update` writes to a temporary sibling file, flushes it, and replaces the destination atomically only after successful discovery and serialization.
- `check` never writes the lockfile.
- The default timeout is 30 seconds per request and can be changed with `--timeout`.
- A timeout, unsupported protocol version, missing `tools` capability, malformed JSON-RPC response, invalid tool shape, duplicate tool name, repeated pagination cursor, premature process exit, or absent lockfile is an exit-code `2` failure with an actionable message.
- Every non-empty stdout line must be one UTF-8 JSON-RPC message. Logging or other contamination on stdout is reported as a protocol error.
- Stderr is drained concurrently to prevent deadlock. On failure, MCPLock displays at most the final 32 KiB and never persists it.
- Incoming messages larger than 16 MiB are rejected to bound memory consumption.
- The server command is always passed as an argument vector with `shell=False`.
- MCPLock captures neither a modified environment nor secrets. The child inherits the caller's environment because many MCP servers require credentials, but those values are never serialized or printed by MCPLock.
- Keyboard interruption triggers child cleanup and exit code `130`.

## Verification strategy

Development follows test-driven implementation using only `unittest` and standard-library helpers.

### Unit coverage

- deterministic canonicalization and serialization;
- every breaking, warning, and informational comparison rule;
- nested inline properties;
- context-size thresholds;
- exit-code mapping; and
- invalid lockfile versions and structures.

### Subprocess integration coverage

A bundled fake MCP server supports deterministic scenarios selected by arguments. Tests cover:

- initialization and capability negotiation;
- multiple `tools/list` pages;
- notifications interleaved with responses;
- server-to-client request rejection;
- malformed stdout and oversized messages;
- stderr capture and truncation;
- request timeout and premature exit;
- duplicate tool names and pagination cycles;
- atomic update behavior; and
- byte-identical repeated lockfile generation.

### Platform coverage

GitHub Actions runs the tests on Linux, macOS, and Windows with supported Python versions. The suite uses no network access and no real model or external MCP server.

## Open-source release shape

The repository contains only what the first useful release needs:

- a PEP 621 `pyproject.toml` exposing the `mcplock` console script;
- the Python package;
- the `unittest` suite and fake server;
- a README with a one-minute offline demonstration and CI example;
- an MIT license;
- concise contribution guidance; and
- one GitHub Actions test workflow.

No plugin system, configuration-file format, extension API, Docker image, website, or generated documentation site is included in V1.

## Acceptance criteria

The first release is complete when:

1. A developer can install and produce a lockfile in under one minute.
2. `update` creates byte-identical output for an unchanged server.
3. `check` follows all pagination and detects every documented certain breaking change.
4. Uncertain changes remain visible as warnings.
5. Failure never overwrites a valid lockfile or leaves a child process running.
6. The complete test suite passes without network access on Linux, macOS, and Windows.
7. The package has zero runtime dependencies and performs no telemetry or outbound network access.

## Deferred evolution

Future releases may add capabilities only in response to demonstrated demand. Plausible additions are the `2026-07-28` protocol era after it becomes stable, Streamable HTTP, machine-readable diff output, stricter CI warning policies, and richer schema compatibility. They are intentionally outside the first implementation plan.
