"""A small Model Context Protocol server over stdio (JSON-RPC 2.0, one message per line).

Hand-written on purpose: the monitor has no third-party dependencies, and the slice of MCP
needed for tools (initialize, ping, tools/list, tools/call) is small. It is checked against the
official SDK's client in the project's test notes.

Rules this file keeps:
- nothing but protocol messages goes to stdout (logs go to stderr);
- a tool failure is a normal result with isError=true, so the model can read the reason;
- arguments are validated against each tool's own JSON schema before the handler runs.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


class ToolError(Exception):
    """A failure to report to the model as the tool's result."""


@dataclass
class ToolResult:
    text: str
    data: dict | list | None = None  # also sent as structuredContent on newer protocol versions


@dataclass
class Tool:
    name: str
    title: str
    description: str
    schema: dict
    handler: Callable[[dict], "ToolResult | str | dict | list"]
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False

    def listing(self):
        return {
            "name": self.name, "title": self.title, "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"title": self.title, "readOnlyHint": self.read_only,
                            "destructiveHint": self.destructive, "idempotentHint": self.idempotent,
                            "openWorldHint": self.open_world},
        }


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate_args(schema, args):
    """Check `args` against the small subset of JSON Schema the tools use. Returns a message or None."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            return f"missing required argument '{name}'"
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            return f"unknown argument '{name}'"
        kind = spec.get("type")
        wrong = (kind in _TYPES and not isinstance(value, _TYPES[kind])) \
            or (kind in ("integer", "number") and isinstance(value, bool))
        if wrong:
            return f"'{name}' must be of type {kind}"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{name}' must be one of {', '.join(map(str, spec['enum']))}"
        if kind in ("integer", "number"):
            if "minimum" in spec and value < spec["minimum"]:
                return f"'{name}' must be at least {spec['minimum']}"
            if "maximum" in spec and value > spec["maximum"]:
                return f"'{name}' must be at most {spec['maximum']}"
        if kind == "string" and "maxLength" in spec and len(value) > spec["maxLength"]:
            return f"'{name}' is too long (limit {spec['maxLength']} characters)"
    return None


@dataclass
class McpServer:
    name: str
    version: str
    tools: list[Tool]
    instructions: str = ""
    protocol: str = SUPPORTED_VERSIONS[0]
    _by_name: dict = field(init=False, default_factory=dict)

    def __post_init__(self):
        self._by_name = {t.name: t for t in self.tools}

    # --- message handling -------------------------------------------------------
    def handle(self, message, protocol=None):
        """Process one decoded JSON-RPC message; returns the response, or None for notifications.

        `protocol` is the version a session negotiated. stdio has one session, so it defaults to
        the version remembered from the last initialize; HTTP passes each session's own.
        """
        if isinstance(message, list):  # batches were allowed before 2025-06-18
            replies = [r for r in (self.handle(m, protocol) for m in message) if r is not None]
            return replies or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return self._error(message.get("id") if isinstance(message, dict) else None,
                               INVALID_REQUEST, "invalid request")
        method, mid = message["method"], message.get("id")
        if "id" not in message:  # a notification: never answered
            return None
        params = message.get("params") or {}
        try:
            if method == "initialize":
                return self._ok(mid, self._initialize(params))
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, {"tools": [t.listing() for t in self.tools]})
            if method == "tools/call":
                return self._call(mid, params, protocol or self.protocol)
        except Exception as exc:  # a bug must not kill the connection
            print(f"velop-doctor mcp: internal error in {method}: {exc!r}", file=sys.stderr)
            return self._error(mid, -32603, "internal error")
        return self._error(mid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params):
        wanted = params.get("protocolVersion")
        self.protocol = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        result = {"protocolVersion": self.protocol,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": self.name, "version": self.version}}
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _call(self, mid, params, protocol):
        tool = self._by_name.get(params.get("name"))
        if tool is None:
            return self._error(mid, INVALID_PARAMS, f"unknown tool: {str(params.get('name'))[:60]}")
        args = params.get("arguments") or {}
        problem = validate_args(tool.schema, args)
        if problem:
            return self._ok(mid, self._tool_error(f"Invalid arguments for {tool.name}: {problem}."))
        try:
            out = tool.handler(args)
        except ToolError as exc:
            return self._ok(mid, self._tool_error(str(exc)))
        except Exception as exc:
            print(f"velop-doctor mcp: tool {tool.name} crashed: {exc!r}", file=sys.stderr)
            return self._ok(mid, self._tool_error(f"{tool.name} failed unexpectedly."))
        return self._ok(mid, self._tool_result(out, protocol))

    def _tool_result(self, out, protocol):
        if isinstance(out, ToolResult):
            text, data = out.text, out.data
        elif isinstance(out, str):
            text, data = out, None
        else:
            text, data = json.dumps(out, indent=2), out
        result = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(data, dict) and protocol >= "2025-06-18":
            result["structuredContent"] = data  # the spec wants an object here, never a list
        return result

    @staticmethod
    def _tool_error(message):
        return {"content": [{"type": "text", "text": message}], "isError": True}

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    # --- stdio transport ------------------------------------------------------------
    def serve_stdio(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                reply = self._error(None, PARSE_ERROR, "parse error")
            else:
                reply = self.handle(message)
            if reply is not None:
                stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
                stdout.flush()
