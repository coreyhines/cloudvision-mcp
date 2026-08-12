import logging

from arista.endpointlocation.v1 import models, services
from google.protobuf import wrappers_pb2 as wrappers

from .models import EndpointLookupResult
from .utils import RPC_TIMEOUT, convert_response_to_endpoint_location


def _device_map_entries(endpoint_location):
    if not endpoint_location.HasField("device_map"):
        return []
    return list(endpoint_location.device_map.values.items())


def _dedupe_endpoints(endpoints: list) -> list:
    seen: set[tuple] = set()
    out: list = []
    for ep in endpoints:
        key = (ep.get("mac_address"), ep.get("ip_address"), ep.get("hostname"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ep)
    return out


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


def _grpc_endpoints_via_getsome(stub, keys: list[str]) -> EndpointLookupResult:
    req = services.EndpointLocationSomeRequest(
        keys=[
            models.EndpointLocationKey(search_term=wrappers.StringValue(value=k))
            for k in keys
        ]
    )
    endpoints: list = []
    hits = 0
    misses = 0
    warnings: list[str] = []
    for resp in stub.GetSome(req, timeout=RPC_TIMEOUT):
        if resp.HasField("error") and resp.error.value:
            misses += 1
            warnings.append(f"getsome_key_error:{resp.error.value}")
            continue
        if not resp.HasField("value"):
            misses += 1
            continue
        batch = []
        for _k, device in _device_map_entries(resp.value):
            batch.append(convert_response_to_endpoint_location(device))
        if batch:
            hits += 1
            endpoints.extend(batch)
        else:
            misses += 1
    return {
        "endpoints": _dedupe_endpoints(endpoints),
        "hits": hits,
        "misses": misses,
        "warnings": warnings,
        "method": "getsome",
    }


def grpc_endpoints_for_search_keys(
    channel, search_keys: list[str]
) -> EndpointLookupResult:
    keys = [k for k in (search_keys or []) if k]
    if not keys:
        return {
            "endpoints": [],
            "hits": 0,
            "misses": 0,
            "warnings": ["no_search_keys"],
            "method": "getsome",
        }
    stub = services.EndpointLocationServiceStub(channel)
    try:
        return _grpc_endpoints_via_getsome(stub, keys)
    except Exception as e:
        logging.error("EndpointLocation GetSome failed: %s", e)
        warnings = [f"getsome_failed:{e}"]
        endpoints: list = []
        hits = 0
        for key in keys:
            found = grpc_one_endpoint_location(channel, key)
            if found:
                hits += 1
                endpoints.extend(found)
        return {
            "endpoints": _dedupe_endpoints(endpoints),
            "hits": hits,
            "misses": len(keys) - hits,
            "warnings": warnings + ["fell_back_to_getone"],
            "method": "getone",
        }


def endpoint_location_matches_filters(
    endpoint: dict,
    *,
    device_id: str | None = None,
    interface: str | None = None,
    vlan_id: int | None = None,
) -> bool:
    locs = endpoint.get("location_list") or []
    if not locs:
        return not device_id and not interface and vlan_id is None
    for loc in locs:
        if device_id:
            did = (loc.get("device_id") or {}).get("value")
            if did != device_id:
                continue
        if interface:
            iface = (loc.get("interface") or {}).get("value")
            if iface != interface:
                continue
        if vlan_id is not None:
            vid = (loc.get("vlan_id") or {}).get("value")
            if vid != vlan_id:
                continue
        return True
    return False
