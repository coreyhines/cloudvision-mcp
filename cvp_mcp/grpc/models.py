from typing import TypedDict


class SwitchInfo(TypedDict):
    hostname: str
    model: str
    serial_number: str
    system_mac: str
    version: str
    streaming_status: str
    device_type: str
    hardware_revision: str
    fqdn: str
    domain_name: str


class BugExposure(TypedDict):
    serial_number: str
    bug_ids: list[int]
    cve_ids: list[int]
    bug_count: int
    cve_count: int
    highest_cve_exposure: str
    highest_bug_exposure: str


class ProbeStats(TypedDict):
    serial_number: str
    host: str
    vrf: str
    source_intf: str
    latency_millis: float
    jitter_millis: float
    http_response_time_millis: float
    packet_loss_percent: int
    error: str


class DeviceSoftwareEoL(TypedDict):
    version: str
    end_of_support: str


class DeviceHardwareEoL(TypedDict):
    end_of_life: str
    end_of_sale: str
    end_of_tac_support: str
    end_of_hardware_rma_requests: str


class DeviceLifecycleSummary(TypedDict):
    serial_number: str
    software_eol: DeviceSoftwareEoL
    hardware_lifecycle_summary: DeviceHardwareEoL


# ===================================================
# Endpoint Location Models
# ===================================================


class EndpointLocationList(TypedDict):
    serial_number: str
    device_status: str
    interface: str
    vlan_id: int
    learned_time: str
    mac_type: str
    likelihood: str


class EndpointLocation(TypedDict):
    hostname: str
    ip_address: str
    mac_address: str
    location_list: list[dict]


class FlowRecord(TypedDict, total=False):
    device_id: str
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: str
    vrf_name: str
    bytes_count: int
    packet_count: int
    start_time: str
    end_time: str
    ingress_interface: str
    egress_interface: str
    flow_type: str  # e.g. Clover / IPFIX
    flow_path_index: str  # index under .../path/{n} when present
    applications: list[dict]
