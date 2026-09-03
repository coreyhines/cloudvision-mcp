"""CloudVision studio and workspace read member callables."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.studio_tags import (
    get_cvp_studio_assigned_tags as grpc_get_studio_assigned_tags,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studio as grpc_get_studio,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studio_inputs as grpc_get_studio_inputs,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studios as grpc_get_studios,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspace as grpc_get_workspace,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspace_build as grpc_get_workspace_build,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspaces as grpc_get_workspaces,
)
from cvp_mcp.grpc.studios import (
    search_cvp_studio_templates as grpc_search_studio_templates,
)


def _studio_call(
    failure_code: str,
    context: str,
    call: Callable[..., dict],
    *args: Any,
    **kwargs: Any,
) -> dict:
    datadict = env_datadict_from_os()
    try:
        return call(datadict, *args, **kwargs)
    except Exception as exc:
        return client_error(failure_code, log_exc=exc, context=context)


def studios_list() -> dict:
    """Return studio summaries."""
    return _studio_call("studios_failed", "get_cvp_studios", grpc_get_studios)


def studios_get(
    studio_id: str, workspace_id: str | None = None, body: bool = False
) -> dict:
    """Return one studio."""
    return _studio_call(
        "studio_failed",
        "get_cvp_studio",
        grpc_get_studio,
        studio_id,
        workspace_id,
        body=body,
    )


def studios_inputs(studio_id: str, workspace_id: str | None = None) -> dict:
    """Return studio input documents."""
    return _studio_call(
        "studio_inputs_failed",
        "get_cvp_studio_inputs",
        grpc_get_studio_inputs,
        studio_id,
        workspace_id,
    )


def studios_search_templates(
    pattern: str, include_input_schema: bool = True, max_hits: int = 100
) -> dict:
    """Search studio templates and schemas."""
    return _studio_call(
        "studio_search_failed",
        "search_cvp_studio_templates",
        grpc_search_studio_templates,
        pattern,
        include_input_schema=include_input_schema,
        max_hits=max_hits,
    )


def studios_list_workspaces() -> dict:
    """Return workspace summaries."""
    return _studio_call("workspaces_failed", "get_cvp_workspaces", grpc_get_workspaces)


def studios_get_workspace(workspace_id: str) -> dict:
    """Return one workspace."""
    return _studio_call(
        "workspace_failed", "get_cvp_workspace", grpc_get_workspace, workspace_id
    )


def studios_get_build(workspace_id: str, build_id: str) -> dict:
    """Return one workspace build."""
    return _studio_call(
        "workspace_build_failed",
        "get_cvp_workspace_build",
        grpc_get_workspace_build,
        workspace_id,
        build_id,
    )


def studios_tags(studio_id: str, workspace_id: str | None = None) -> dict:
    """Return tags assigned to one studio."""
    return _studio_call(
        "studio_assigned_tags_failed",
        "get_cvp_studio_assigned_tags",
        grpc_get_studio_assigned_tags,
        studio_id,
        workspace_id,
    )


def members() -> dict[str, MemberSpec]:
    """Return studio member specifications keyed by action."""
    studio_id = {"studio_id": {"type": "string", "description": "Studio id."}}
    workspace_id = {"workspace_id": {"type": "string", "description": "Workspace id."}}
    return {
        "list": MemberSpec(
            action="list",
            description="List studio summaries.",
            required=[],
            properties={},
            call=studios_list,
        ),
        "get": MemberSpec(
            action="get",
            description="Get one studio, optionally including its template body.",
            required=["studio_id"],
            properties={
                **studio_id,
                **workspace_id,
                "body": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include the full Mako template body.",
                },
            },
            call=studios_get,
        ),
        "inputs": MemberSpec(
            action="inputs",
            description="Get studio input documents.",
            required=["studio_id"],
            properties={**studio_id, **workspace_id},
            call=studios_inputs,
        ),
        "search_templates": MemberSpec(
            action="search_templates",
            description="Search studio templates and input schemas.",
            required=["pattern"],
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Literal template search text.",
                },
                "include_input_schema": {
                    "type": "boolean",
                    "default": True,
                    "description": "Search input schemas too.",
                },
                "max_hits": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum matching paths.",
                },
            },
            call=studios_search_templates,
        ),
        "list_workspaces": MemberSpec(
            action="list_workspaces",
            description="List workspaces.",
            required=[],
            properties={},
            call=studios_list_workspaces,
        ),
        "get_workspace": MemberSpec(
            action="get_workspace",
            description="Get one workspace.",
            required=["workspace_id"],
            properties=workspace_id,
            call=studios_get_workspace,
        ),
        "get_build": MemberSpec(
            action="get_build",
            description="Get workspace build status.",
            required=["workspace_id", "build_id"],
            properties={
                **workspace_id,
                "build_id": {
                    "type": "string",
                    "description": "Workspace build id.",
                },
            },
            call=studios_get_build,
        ),
        "tags": MemberSpec(
            action="tags",
            description="Get tags assigned to a studio.",
            required=["studio_id"],
            properties={**studio_id, **workspace_id},
            call=studios_tags,
        ),
    }
