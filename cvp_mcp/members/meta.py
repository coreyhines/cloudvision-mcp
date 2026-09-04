"""CloudVision API metadata member callables."""

from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.capability import probe_arista_v1_packages


def meta_probe_apis() -> dict:
    """Return installed Arista Resource API Python packages."""
    return {"packages": probe_arista_v1_packages()}


def members() -> dict[str, MemberSpec]:
    """Return metadata member specifications keyed by action."""
    return {
        "probe_apis": MemberSpec(
            action="probe_apis",
            description="List installed arista.*.v1 Resource API packages.",
            required=[],
            properties={},
            call=meta_probe_apis,
        )
    }
