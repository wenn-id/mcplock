import argparse
import json
import os
import subprocess
import sys
import time


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
        choices=[
            "baseline",
            "breaking",
            "pagination",
            "legacy",
            "notification",
            "notification-stream",
            "boolean-id",
            "client-request",
            "duplicate",
            "cycle",
            "invalid-tool",
            "too-many-pages",
            "too-many-tools",
            "invalid-cursor",
            "rpc-error",
            "bad-jsonrpc",
            "malformed",
            "stderr-exit",
            "stderr-large",
            "stderr-invalid",
            "stderr-descendant",
            "hang",
            "missing-tools",
            "unsupported",
            "oversized",
        ],
        default="baseline",
    )
    args = parser.parse_args()

    if args.scenario == "malformed":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        time.sleep(1)
        return
    if args.scenario == "bad-jsonrpc":
        send({"id": 1, "result": {}})
        time.sleep(1)
        return
    if args.scenario == "stderr-exit":
        sys.stderr.write("fixture launch failed: secret-free diagnostic\n")
        sys.stderr.flush()
        return
    if args.scenario == "stderr-large":
        sys.stderr.buffer.write(b"x" * 40000 + b"TAIL\n")
        sys.stderr.buffer.flush()
        return
    if args.scenario == "stderr-invalid":
        sys.stderr.buffer.write(b"\xff" * 40000 + b"TAIL\n")
        sys.stderr.buffer.flush()
        return
    if args.scenario == "stderr-descendant":
        pid_file = os.environ["MCPLOCK_DESCENDANT_PID_FILE"]
        descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=sys.stderr,
        )
        with open(pid_file, "w", encoding="ascii") as handle:
            handle.write(str(descendant.pid))
    if args.scenario == "hang":
        time.sleep(10)
        return
    if args.scenario == "oversized":
        sys.stdout.buffer.write(
            b'{"jsonrpc":"2.0","note":"' + b"x" * (16 * 1024 * 1024) + b'"}\n'
        )
        sys.stdout.buffer.flush()
        time.sleep(1)
        return

    initialize = receive()
    if args.scenario == "notification":
        send({"jsonrpc": "2.0", "method": "notifications/progress"})
    if args.scenario == "notification-stream":
        for _ in range(22):
            send({"jsonrpc": "2.0", "method": "notifications/progress"})
            time.sleep(0.05)
    if args.scenario == "client-request":
        send({"jsonrpc": "2.0", "id": 900, "method": "roots/list"})
        rejection = receive()
        assert rejection["id"] == 900
        assert rejection["error"]["code"] == -32601

    if args.scenario == "legacy":
        version = "2024-11-05"
    elif args.scenario == "unsupported":
        version = "2099-01-01"
    else:
        version = "2025-11-25"
    capabilities = {} if args.scenario == "missing-tools" else {"tools": {}}
    send({
        "jsonrpc": "2.0",
        "id": True if args.scenario == "boolean-id" else initialize["id"],
        "result": {
            "protocolVersion": version,
            "capabilities": capabilities,
            "serverInfo": {"name": "fixture", "version": "1.0.0"},
        },
    })
    initialized = receive()
    assert initialized["method"] == "notifications/initialized"

    request = receive()
    assert request["method"] == "tools/list"
    if args.scenario == "rpc-error":
        send({
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32603, "message": "fixture failure"},
        })
        return
    if args.scenario == "too-many-pages":
        page = 0
        while True:
            page += 1
            send({
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [tool(f"tool_{page}")],
                    "nextCursor": f"p{page}",
                },
            })
            request = receive()
        return
    if args.scenario == "too-many-tools":
        send({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "tools": [tool(f"tool_{i}") for i in range(4)],
            },
        })
        return
    if args.scenario == "cycle":
        for _ in range(2):
            send({
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [tool("read_file")],
                    "nextCursor": "again",
                },
            })
            request = receive()
            assert request["params"]["cursor"] == "again"
        return
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
    elif args.scenario == "duplicate":
        tools = [tool("same"), tool("same")]
    elif args.scenario == "invalid-tool":
        tools = [{"inputSchema": {}}]
    else:
        tools = [tool("read_file")]

    result = {"tools": tools}
    if args.scenario == "invalid-cursor":
        result["nextCursor"] = 7
    send({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": result,
    })


if __name__ == "__main__":
    main()
