"""Canonical digest of a parsed studio Inputs document.

Shared by the Phase 1 read (``get_cvp_studio_inputs`` reports ``inputs_sha256``)
and the Phase 2.3 MSS root write (``expected_inputs_sha256`` CAS). Both sides
must hash the same way, so this module holds the one definition and imports
nothing from ``cvp_mcp.grpc`` (``studios_write`` imports ``studios``; a digest
living in either would make the other side a cycle).

The digest is over the **parsed** document with sorted keys and compact
separators, so wire key order and whitespace do not matter. It is defined only
for ``dict`` / ``list``: a row whose ``inputs`` did not parse gets ``None``
rather than a digest the write side could never reproduce.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(document: Any) -> str:
    """Sorted-key, compact JSON used for digests and for the structural diff."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)


def inputs_sha256(document: Any) -> str | None:
    """SHA-256 hex of :func:`canonical_json`, or ``None`` for non-container input."""
    if not isinstance(document, (dict, list)):
        return None
    return hashlib.sha256(canonical_json(document).encode()).hexdigest()
