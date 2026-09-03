"""CloudVision studio and workspace write member callables."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.studio_crud import (
    create_cvp_studio as grpc_create_studio,
)
from cvp_mcp.grpc.studio_crud import (
    delete_cvp_studio as grpc_delete_studio,
)
from cvp_mcp.grpc.studio_inputs_generic import (
    set_cvp_studio_inputs as grpc_set_studio_inputs,
)
from cvp_mcp.grpc.studio_mss_inputs import (
    set_cvp_mss_policy_inputs as grpc_set_mss_policy_inputs,
)
from cvp_mcp.grpc.studio_tags import (
    assign_cvp_studio_tags as grpc_assign_studio_tags,
)
from cvp_mcp.grpc.studios_write import (
    build_cvp_workspace as grpc_build_workspace,
)
from cvp_mcp.grpc.studios_write import (
    create_cvp_workspace as grpc_create_workspace,
)
from cvp_mcp.grpc.studios_write import (
    delete_cvp_workspace as grpc_delete_workspace,
)
from cvp_mcp.grpc.studios_write import (
    set_cvp_access_interface_description as grpc_set_access_description,
)


def _write_call(
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


def studios_write_create_workspace(
    workspace_id: str,
    display_name: str,
    description: str = "",
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Create a draft workspace."""
    return _write_call(
        "create_workspace_failed",
        "create_cvp_workspace",
        grpc_create_workspace,
        workspace_id,
        display_name,
        description=description,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_delete_workspace(
    workspace_id: str,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Delete a pending draft workspace."""
    return _write_call(
        "delete_workspace_failed",
        "delete_cvp_workspace",
        grpc_delete_workspace,
        workspace_id,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_build(
    workspace_id: str,
    request_id: str | None = None,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Start a workspace build."""
    return _write_call(
        "build_workspace_failed",
        "build_cvp_workspace",
        grpc_build_workspace,
        workspace_id,
        request_id=request_id,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_set_description(
    workspace_id: str,
    device_id: str,
    interface: str,
    expected_current_description: str,
    new_description: str,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Compare and set one access-interface description."""
    return _write_call(
        "set_access_description_failed",
        "set_cvp_access_interface_description",
        grpc_set_access_description,
        workspace_id,
        device_id,
        interface,
        expected_current_description,
        new_description,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_set_inputs(
    studio_id: str,
    workspace_id: str,
    path_values: list[str],
    inputs: Any,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Set one studio input subtree."""
    return _write_call(
        "set_studio_inputs_failed",
        "set_cvp_studio_inputs",
        grpc_set_studio_inputs,
        studio_id,
        workspace_id,
        path_values,
        inputs,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_assign_tags(
    studio_id: str,
    workspace_id: str,
    query: str,
    expected_current_query: str,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Compare and set a studio tag query."""
    return _write_call(
        "assign_studio_tags_failed",
        "assign_cvp_studio_tags",
        grpc_assign_studio_tags,
        studio_id,
        workspace_id,
        query,
        expected_current_query,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_create_studio(
    workspace_id: str,
    studio_id: str,
    display_name: str,
    template_body: str = "",
    description: str = "",
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Create a studio in a draft workspace."""
    return _write_call(
        "create_studio_failed",
        "create_cvp_studio",
        grpc_create_studio,
        workspace_id,
        studio_id,
        display_name,
        template_body=template_body,
        description=description,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_delete_studio(
    workspace_id: str,
    studio_id: str,
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Delete a studio from a draft workspace."""
    return _write_call(
        "delete_studio_failed",
        "delete_cvp_studio",
        grpc_delete_studio,
        workspace_id,
        studio_id,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def studios_write_set_mss_inputs(
    workspace_id: str,
    expected_inputs_sha256: str,
    operations: list[dict],
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    """Compare and set MSS policy inputs."""
    return _write_call(
        "set_mss_policy_inputs_failed",
        "set_cvp_mss_policy_inputs",
        grpc_set_mss_policy_inputs,
        workspace_id,
        expected_inputs_sha256,
        operations,
        confirm=confirm,
        preview_token_value=preview_token,
    )


def members() -> dict[str, MemberSpec]:
    """Return studio write member specifications keyed by action."""
    workspace_id = {
        "workspace_id": {
            "type": "string",
            "description": "Draft workspace id starting with ws-mcp-*.",
        }
    }
    studio_id = {"studio_id": {"type": "string", "description": "Studio id."}}
    confirmation = {
        "confirm": {"type": "boolean", "default": False},
        "preview_token": {"type": "string"},
    }
    return {
        "create_workspace": MemberSpec(
            action="create_workspace",
            description="Create a draft workspace.",
            required=["workspace_id", "display_name"],
            properties={
                **workspace_id,
                "display_name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                **confirmation,
            },
            call=studios_write_create_workspace,
        ),
        "delete_workspace": MemberSpec(
            action="delete_workspace",
            description="Delete a pending draft workspace.",
            required=["workspace_id"],
            properties={**workspace_id, **confirmation},
            call=studios_write_delete_workspace,
        ),
        "build": MemberSpec(
            action="build",
            description="Start a workspace build.",
            required=["workspace_id"],
            properties={
                **workspace_id,
                "request_id": {"type": "string"},
                **confirmation,
            },
            call=studios_write_build,
        ),
        "set_description": MemberSpec(
            action="set_description",
            description="Compare and set one access-interface description.",
            required=[
                "workspace_id",
                "device_id",
                "interface",
                "expected_current_description",
                "new_description",
            ],
            properties={
                **workspace_id,
                "device_id": {"type": "string"},
                "interface": {"type": "string"},
                "expected_current_description": {"type": "string"},
                "new_description": {"type": "string"},
                **confirmation,
            },
            call=studios_write_set_description,
        ),
        "set_inputs": MemberSpec(
            action="set_inputs",
            description="Set one non-root studio input subtree.",
            required=["studio_id", "workspace_id", "path_values", "inputs"],
            properties={
                **studio_id,
                **workspace_id,
                "path_values": {"type": "array", "items": {"type": "string"}},
                "inputs": {},
                **confirmation,
            },
            call=studios_write_set_inputs,
        ),
        "assign_tags": MemberSpec(
            action="assign_tags",
            description="Compare and set a studio tag query.",
            required=[
                "studio_id",
                "workspace_id",
                "query",
                "expected_current_query",
            ],
            properties={
                **studio_id,
                **workspace_id,
                "query": {"type": "string"},
                "expected_current_query": {"type": "string"},
                **confirmation,
            },
            call=studios_write_assign_tags,
        ),
        "create_studio": MemberSpec(
            action="create_studio",
            description="Create a studio in a draft workspace.",
            required=["workspace_id", "studio_id", "display_name"],
            properties={
                **workspace_id,
                **studio_id,
                "display_name": {"type": "string"},
                "template_body": {"type": "string", "default": ""},
                "description": {"type": "string", "default": ""},
                **confirmation,
            },
            call=studios_write_create_studio,
        ),
        "delete_studio": MemberSpec(
            action="delete_studio",
            description="Delete a studio from a draft workspace.",
            required=["workspace_id", "studio_id"],
            properties={**workspace_id, **studio_id, **confirmation},
            call=studios_write_delete_studio,
        ),
        "set_mss_inputs": MemberSpec(
            action="set_mss_inputs",
            description="Compare and set MSS policy inputs.",
            required=["workspace_id", "expected_inputs_sha256", "operations"],
            properties={
                **workspace_id,
                "expected_inputs_sha256": {"type": "string"},
                "operations": {"type": "array", "items": {"type": "object"}},
                **confirmation,
            },
            call=studios_write_set_mss_inputs,
        ),
    }
