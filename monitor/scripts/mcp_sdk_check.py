#!/usr/bin/env python3
"""Drive `velop-doctor mcp` with the OFFICIAL MCP SDK client, the way Claude does.

This is a manual interoperability check, not part of the unit tests, because it needs a
third-party package the monitor itself never depends on:

    python3 -m venv /tmp/mcp-venv && /tmp/mcp-venv/bin/pip install mcp
    PYTHONPATH=monitor/src python3 -m velop_doctor serve --demo --port 8791 &
    /tmp/mcp-venv/bin/python monitor/scripts/mcp_sdk_check.py 8791            # stdio

    # over HTTP: start `velop-doctor mcp --http --port 8791` too, then
    /tmp/mcp-venv/bin/python monitor/scripts/mcp_sdk_check.py 8791 --http     # token from ~/.velop-doctor/mcp-token

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


async def check(session):
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
    bad = await session.call_tool("ping", {"host": "-c 99999"})
    assert bad.is_error, "a hostile host must be refused"
    print("hostile host refused:", bad.content[0].text)
    print("OK")


async def main_http():
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    token = os.environ.get("VELOP_DOCTOR_MCP_TOKEN") or (Path.home() / ".velop-doctor" / "mcp-token").read_text().strip()
    url = os.environ.get("VELOP_DOCTOR_MCP_URL", "http://localhost:8780/mcp")
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await check(session)


async def main():
    if "--http" in sys.argv:
        return await main_http()
    params = StdioServerParameters(
        command=sys.executable if "--same-python" in sys.argv else "python3",
        args=["-m", "velop_doctor", "mcp", "--port", PORT],
        env={**os.environ, "PYTHONPATH": str(SRC)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await check(session)

asyncio.run(main())
