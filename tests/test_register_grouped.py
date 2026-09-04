import asyncio

from mcp.server.fastmcp import FastMCP

from cvp_mcp.grouped_tool import GroupedTool, MemberSpec
from cvp_mcp.register_grouped import register_grouped_tool


def test_register_exposes_enum_and_dispatches():
    mcp = FastMCP("test")
    group = GroupedTool(
        name="meta",
        description="Meta",
        members={
            "probe_apis": MemberSpec(
                action="probe_apis",
                description="Probe",
                required=[],
                properties={},
                call=lambda: {"ok": True},
            )
        },
    )
    register_grouped_tool(mcp, group)
    tools = asyncio.run(mcp.list_tools())
    assert tools[0].name == "meta"
    assert "help" in tools[0].inputSchema["properties"]["action"]["enum"]
    result = asyncio.run(mcp.call_tool("meta", {"action": "probe_apis"}))
    # SDK wraps dict returns as TextContent JSON; assert payload contains ok
    text = result[0].text if isinstance(result, list) else str(result)
    assert "ok" in text
