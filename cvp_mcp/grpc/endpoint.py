import logging

from arista.endpointlocation.v1 import models, services
from google.protobuf import wrappers_pb2 as wrappers

from .utils import RPC_TIMEOUT, convert_response_to_endpoint_location


def _device_map_entries(endpoint_location):
    if not endpoint_location.HasField("device_map"):
        return []
    return list(endpoint_location.device_map.values.items())


def grpc_one_endpoint_location(channel, query):
    """
    Performs a serach to get an endpoint based on search term
    """
    all_endpoints = []
    stub = services.EndpointLocationServiceStub(channel)
    get_all_req = services.EndpointLocationRequest(
        key=models.EndpointLocationKey(search_term=wrappers.StringValue(value=query))
    )
    try:
        endpoints = stub.GetOne(get_all_req, timeout=RPC_TIMEOUT)
        for _key, device in _device_map_entries(endpoints.value):
            logging.debug(f"One PRE PROBE: {device}")
            _endpoint = convert_response_to_endpoint_location(device)
            logging.debug(f"One PROBE: {_endpoint}")
            all_endpoints.append(_endpoint)
        return all_endpoints
    except Exception as e:
        logging.error(f"Error with Endpoint Location: {e}")
        return []


def grpc_all_endpoint_locations(channel):
    all_endpoints = []
    stub = services.EndpointLocationServiceStub(channel)
    stream_req = services.EndpointLocationStreamRequest()
    try:
        for response in stub.GetAll(stream_req, timeout=RPC_TIMEOUT):
            for _key, device in _device_map_entries(response.value):
                _endpoint = convert_response_to_endpoint_location(device)
                all_endpoints.append(_endpoint)
    except Exception as e:
        logging.error(f"Error streaming all endpoint locations: {e}")
    return all_endpoints


def _location_matches_filter(loc, device_id, interface, vlan_id):
    if device_id:
        if not loc.HasField("device_id") or loc.device_id.value != device_id:
            return False
    if interface:
        if not loc.HasField("interface") or loc.interface.value != interface:
            return False
    if vlan_id is not None:
        if not loc.HasField("vlan_id") or loc.vlan_id.value != vlan_id:
            return False
    return True


def grpc_endpoints_by_filter(channel, device_id=None, interface=None, vlan_id=None):
    all_endpoints = []
    stub = services.EndpointLocationServiceStub(channel)
    stream_req = services.EndpointLocationStreamRequest()
    try:
        for response in stub.GetAll(stream_req, timeout=RPC_TIMEOUT):
            for _key, device in _device_map_entries(response.value):
                if not device.HasField("location_list"):
                    continue
                matched = any(
                    _location_matches_filter(loc, device_id, interface, vlan_id)
                    for loc in device.location_list.values
                )
                if matched:
                    all_endpoints.append(convert_response_to_endpoint_location(device))
    except Exception as e:
        logging.error(f"Error filtering endpoint locations: {e}")
    return all_endpoints
