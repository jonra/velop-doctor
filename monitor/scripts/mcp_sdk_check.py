#!/usr/bin/env python3
"""Drive `velop-doctor mcp` with the OFFICIAL MCP SDK client, the way Claude does.

This is a manual interoperability check, not part of the unit tests, because it needs a
third-party package the monitor itself never depends on:

    python3 -m venv /tmp/mcp-venv && /tmp/mcp-venv/bin/pip install mcp
    PYTHONPATH=monitor/src python3 -m velop_doctor serve --demo --port 8791 &
    /tmp/mcp-venv/bin/python monitor/scripts/mcp_sdk_check.py 8791

It is read-only: no action is ever confirmed.
"""
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = Path(__file__).resolve().parents[1] / "src"
PORT = sys.argv[1] if len(sys.argv) > 1 else "8765"


async def main():
    params = StdioServerParameters(
        command=sys.executable if "--same-python" in sys.argv else "python3",
        args=["-m", "velop_doctor", "mcp", "--port", PORT],
        env={**os.environ, "PYTHONPATH": str(SRC)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"initialize: {init.server_info.name} {init.server_info.version}, protocol {init.protocol_version}")
            tools = (await session.list_tools()).tools
            print(f"{len(tools)} tools: {', '.join(t.name for t in tools)}")
            incomplete = [t.name for t in tools
                          if not t.description or t.input_schema.get("type") != "object" or t.annotations is None]
            assert not incomplete, f"missing description, schema or annotations: {incomplete}"
            status = await session.call_tool("get_status", {})
            assert not status.is_error, status.content[0].text
            print(status.content[0].text.splitlines()[0])
            preview = await session.call_tool("reboot_node", {"node": "Hall"})
            print("reboot_node without confirm:", preview.content[0].text.splitlines()[0])
            bad = await session.call_tool("ping", {"host": "-c 99999"})
            assert bad.is_error, "a hostile host must be refused"
            print("hostile host refused:", bad.content[0].text)
            print("OK")


asyncio.run(main())
