#!/usr/bin/python3

import argparse
import logging
import os
import re
import sys

from mcp.server.fastmcp import FastMCP

from cvp_mcp.register_grouped import register_grouped_tool
from cvp_mcp.tool_groups import build_groups, build_write_group
from cvp_mcp.transport_security_config import build_transport_security
from cvp_mcp.write_access import writes_enabled

CVP_TRANSPORT = "grpc"

logging.basicConfig(
    level=logging.INFO,  # Minimum log level
    format="%(asctime)s - %(levelname)s - %(message)s",  # Log message format
)


_NOISY_ACCESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"GET / HTTP/1\.1" 404 Not Found'),
    re.compile(r'"GET /lldp/nodes HTTP/1\.1" 404 Not Found'),
    re.compile(r'"GET /v1/topology HTTP/1\.1" 404 Not Found'),
    re.compile(
        r'"GET /\.(well-known/oauth-protected-resource(?:/mcp)?) HTTP/1\.1" 404 Not Found'
    ),
)

_NOISY_MESSAGE_SUBSTRINGS: tuple[str, ...] = (
    "Error handling POST request",
    "starlette.requests.ClientDisconnect",
    "aborting with incomplete response",
    "reading: context canceled",
    "Stateless session crashed",
    "ClosedResourceError",
)


def _is_noise_record(record: logging.LogRecord) -> bool:
    """
    Filter known noisy disconnect/probe logs from MCP streamable-http usage.

    Keep real backend/tool failures visible while dropping high-volume
    disconnect churn and endpoint-probe 404 spam.
    """
    msg = record.getMessage()
    if any(s in msg for s in _NOISY_MESSAGE_SUBSTRINGS):
        return True
    if record.name == "uvicorn.access":
        return any(p.search(msg) for p in _NOISY_ACCESS_PATTERNS)
    return False


class _NoiseSuppressFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _is_noise_record(record)


def _install_noise_filters() -> None:
    filt = _NoiseSuppressFilter()
    # Root handlers catch most output from this app.
    for handler in logging.getLogger().handlers:
        handler.addFilter(filt)
    # Add explicit logger filters for third-party emitters.
    for name in (
        "uvicorn.access",
        "uvicorn.error",
        "mcp.server.streamable_http",
        "mcp",
        "starlette",
    ):
        logging.getLogger(name).addFilter(filt)


_install_noise_filters()

logging.info("Starting the FastMCP server...")

# Initialize FastMCP server (bind host updated from CLI in main() for HTTP transport)
_mcp_http_host = os.environ.get("CVP_MCP_HTTP_HOST", "127.0.0.1")
mcp = FastMCP(
    name="CVP MCP Server",
    host=_mcp_http_host,
    stateless_http=True,
    log_level="WARNING",
    transport_security=build_transport_security(),
)


for group in build_groups():
    register_grouped_tool(mcp, group)
if writes_enabled():
    register_grouped_tool(mcp, build_write_group())


def main(args):
    """Entry point for the direct execution server."""
    global CVP_TRANSPORT

    if args.debug:
        logging.info("Setting server logging to DEBUG")
        logging.getLogger().setLevel(logging.DEBUG)
    mcp_transport = args.transport
    mcp_port = args.port
    mcp_cvp = args.cvp
    CVP_TRANSPORT = mcp_cvp

    logging.info(f"Starting MCP server via {mcp_transport}")
    logging.info(f"Server connection to CVP via {mcp_cvp}")
    # Adding check as HTTP connection to CVP is currently not supported
    if mcp_cvp == "http":
        logging.warning("HTTP connections to CVP are currently not supported")
        sys.exit(1)
    if mcp_transport == "http":
        mcp.settings.port = mcp_port
        mcp.settings.host = args.host
        if args.host == "0.0.0.0":
            logging.warning(
                "HTTP bound to all interfaces (0.0.0.0). "
                "Place an authenticated reverse proxy in front for remote access."
            )
        logging.info(f"Streamable HTTP Server listening on {args.host}:{mcp_port}")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--transport",
        type=str,
        help="MCP Transport method",
        default="stdio",
        choices=["http", "stdio"],
        required=False,
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        help="Port to run the Streamable HTTP Server",
        default=8000,
        required=False,
    )
    parser.add_argument(
        "--host",
        type=str,
        help="Bind address for Streamable HTTP (default 127.0.0.1; use 0.0.0.0 only behind auth proxy)",
        default="127.0.0.1",
        required=False,
    )
    parser.add_argument(
        "-c",
        "--cvp",
        type=str,
        help="CVP Connection protocol",
        choices=["grpc", "http"],
        default="grpc",
        required=False,
    )
    parser.add_argument(
        "-d", "--debug", help="Enable debug logging", action="store_true"
    )
    args = parser.parse_args()
    main(args)
