from __future__ import annotations

import inspect
from typing import Any

from mcp.server.fastmcp import FastMCP

from cvp_mcp.grouped_tool import GroupedTool


def register_grouped_tool(mcp: FastMCP, group: GroupedTool) -> None:
    props = group.input_schema.get("properties") or {}
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": Any}
    for name in props:
        if name == "action":
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                )
            )
            annotations[name] = str
        else:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Any | None,
                )
            )
            annotations[name] = Any | None

    def handler(**kwargs: Any) -> Any:
        return group.execute(dict(kwargs))

    handler.__name__ = group.name
    handler.__doc__ = group.description
    handler.__signature__ = inspect.Signature(parameters, return_annotation=Any)
    handler.__annotations__ = annotations
    mcp.add_tool(handler, name=group.name, description=group.description)
    # MCP SDK FastMCP has no FunctionTool(parameters=…) (unlike standalone fastmcp).
    # Overwrite list_tools schema after signature-based registration (mcp[cli]>=1.29.1,<2).
    mcp._tool_manager._tools[group.name].parameters = group.input_schema
