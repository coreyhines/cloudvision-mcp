import logging

from arista.connectivitymonitor.v1 import models, services
from google.protobuf import wrappers_pb2 as wrappers

from .utils import RPC_TIMEOUT, convert_response_to_probe_stat


def grpc_all_probe_status(channel):
    """
    Gets all Connectivity Monitor Probe Stats  in CVP
    """
    all_probes = []
    stub = services.ProbeStatsServiceStub(channel)
    get_all_req = services.ProbeStatsStreamRequest()
    for probe in stub.GetAll(get_all_req, timeout=RPC_TIMEOUT):
        try:
            _probe = convert_response_to_probe_stat(probe)
            all_probes.append(_probe)
        except Exception as e:
            logging.error(f"Error with probe: {e}")
    return all_probes


def grpc_one_probe_status(channel, serial_number="", host="", vrf="", sourceIntf=""):
    """
    Gets one Connectivity Monitor Probe Stats in CVP
    """
    all_probes = []
    stub = services.ProbeStatsServiceStub(channel)
    get_all_req = services.ProbeStatsStreamRequest()
    # Every supplied field goes on ONE ProbeStats message. The Resource API ORs
    # repeated partial_eq_filter entries, so appending one entry per field would
    # union the filters instead of intersecting them.
    key_fields = {}
    if serial_number:
        key_fields["device_id"] = wrappers.StringValue(value=serial_number)
    if host:
        key_fields["host"] = wrappers.StringValue(value=host)
    if vrf:
        key_fields["vrf"] = wrappers.StringValue(value=vrf)
    if sourceIntf:
        key_fields["source_intf"] = wrappers.StringValue(value=sourceIntf)
    if key_fields:
        get_all_req.partial_eq_filter.append(
            models.ProbeStats(key=models.ProbeStatsKey(**key_fields))
        )
    try:
        for _probe in stub.GetAll(get_all_req, timeout=RPC_TIMEOUT):
            logging.debug(f"One PRE PROBE: {_probe}")
            probe = convert_response_to_probe_stat(_probe)
            logging.debug(f"One PROBE: {probe}")
            all_probes.append(probe)
        return all_probes
    except Exception as e:
        logging.error(f"Error with probe: {e}")
        return []
