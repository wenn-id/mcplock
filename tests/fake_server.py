import argparse
import json
import sys


def receive():
    line = sys.stdin.buffer.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)


def send(message):
    sys.stdout.buffer.write(
        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    sys.stdout.buffer.flush()


def tool(name):
    return {
        "name": name,
        "description": f"Fixture tool {name}",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=["baseline", "breaking", "pagination", "legacy"],
        default="baseline",
    )
    args = parser.parse_args()

    initialize = receive()
    version = "2024-11-05" if args.scenario == "legacy" else "2025-11-25"
    send({
        "jsonrpc": "2.0",
        "id": initialize["id"],
        "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "1.0.0"},
        },
    })
    initialized = receive()
    assert initialized["method"] == "notifications/initialized"

    request = receive()
    assert request["method"] == "tools/list"
    if args.scenario == "pagination":
        send({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": [tool("alpha")], "nextCursor": "page-2"},
        })
        request = receive()
        assert request["params"]["cursor"] == "page-2"
        tools = [tool("zeta")]
    elif args.scenario == "breaking":
        changed = tool("read_file")
        changed["inputSchema"]["properties"]["encoding"] = {"type": "string"}
        changed["inputSchema"]["required"].append("encoding")
        tools = [changed]
    else:
        tools = [tool("read_file")]

    send({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"tools": tools},
    })


if __name__ == "__main__":
    main()
