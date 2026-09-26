FROM docker.io/python:3.13

# Patch OS packages (e.g. libunbound8 CRITICAL CVEs) before app install.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install uv \
    && useradd -m -u 10001 cvpmcp

WORKDIR /workspace

COPY . .

RUN uv sync && chown -R cvpmcp:cvpmcp /workspace

USER cvpmcp

# HTTP inside the container; publish ports only on trusted networks and place an
# authenticated reverse proxy in front for remote/WAN access (see README Security).
ENTRYPOINT [ "uv", "run", "cloudvision_mcp.py", "--transport", "http", "--host", "0.0.0.0" ]
