# Contributing

MCPLock intentionally keeps a small standard-library core.

1. Use Python 3.11 or newer.
2. Install with `python -m pip install -e . --no-deps`.
3. Add a failing `unittest` for every behavior change.
4. Run `python -m unittest discover -s tests -v`.
5. Keep runtime dependencies at zero and tests offline.

Bug reports should include the MCPLock command, exit code, sanitized stderr,
operating system, Python version, and a minimal MCP server fixture when
possible. Never include credentials or an unsanitized environment.
