"""CloudVision API metadata member callables."""

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.capability import probe_arista_v1_packages
from cvp_mcp.grpc.device_resolve import resolve_device_to_serial
from cvp_mcp.grpc.path_probe import probe_device_path


def meta_probe_apis() -> dict:
    """Return installed Arista Resource API Python packages."""
    return {"packages": probe_arista_v1_packages()}


def meta_probe_path(device_id: str, path: str) -> dict:
    """Report what a raw device streaming path returns."""
    datadict = env_datadict_from_os()
    serial, _info, warnings, _candidates = resolve_device_to_serial(datadict, device_id)
    result = probe_device_path(datadict, serial or device_id, path)
    if warnings and isinstance(result, dict):
        result["warnings"] = list(result.get("warnings") or []) + list(warnings)
    return result


def members() -> dict[str, MemberSpec]:
    """Return metadata member specifications keyed by action."""
    return {
        "probe_apis": MemberSpec(
            action="probe_apis",
            description="List installed arista.*.v1 Resource API packages.",
            required=[],
            properties={},
            call=meta_probe_apis,
        ),
        "probe_path": MemberSpec(
            action="probe_path",
            description=(
                "Diagnostic: report what a raw device streaming path returns. "
                "Use '*' for a wildcard segment, e.g. 'Sysdb/environment/*'."
            ),
            required=["device_id", "path"],
            properties={
                "device_id": {
                    "type": "string",
                    "description": "Device serial, hostname, FQDN, or system MAC.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Slash-separated path below the device dataset, with '*' "
                        "for a wildcard segment. Example: Sysdb/environment/*"
                    ),
                },
            },
            call=meta_probe_path,
        ),
    }
