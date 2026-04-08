"""Lightweight introspection of installed Arista API packages (Resource APIs)."""

from __future__ import annotations

import pkgutil


def probe_arista_v1_packages() -> list[str]:
    """Return dotted package names for top-level ``arista.*.v1`` API bundles."""
    try:
        import arista  # type: ignore[import-untyped]
    except ImportError:
        return []

    names: set[str] = set()
    for mod in pkgutil.walk_packages(arista.__path__, arista.__name__ + "."):
        if not mod.ispkg:
            continue
        if ".v1" in mod.name and mod.name.count(".") <= 3:
            names.add(mod.name)
    return sorted(names)
