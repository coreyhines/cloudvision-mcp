"""Grouped MCP tool dispatcher: one tool name, many actions selected by ``action``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cvp_mcp.rate_limit import check_rate_limit
from cvp_mcp.schema_fields import SHARED_FIELDS, is_shared
from cvp_mcp.tool_access import disabled_tools, is_tool_disabled

HELP_ACTION = "help"


def _is_absent(value: Any) -> bool:
    """Treat None, empty, and whitespace-only strings as missing."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


@dataclass
class MemberSpec:
    """One operation behind a grouped tool."""

    action: str
    description: str
    required: list[str]
    properties: dict[str, dict[str, Any]]
    call: Callable[..., Any]
    rate_limit_key: str | None = None


@dataclass
class GroupedTool:
    """Several operations behind one MCP tool name, selected by ``action``."""

    name: str
    description: str
    members: dict[str, MemberSpec]
    field_aliases: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.input_schema = self._build_schema()

    def _aliases_for(self, member: MemberSpec) -> dict[str, str]:
        return self.field_aliases.get(member.action, {})

    def _build_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "action": {
                "type": "string",
                "description": f"Operation to run. '{HELP_ACTION}' lists fields.",
                "enum": [*sorted(self.members), HELP_ACTION],
            }
        }
        used_by: dict[str, list[str]] = {}
        raw: dict[str, dict[str, Any]] = {}

        for action, member in sorted(self.members.items()):
            aliases = self._aliases_for(member)
            for field_name, spec in member.properties.items():
                advertised = aliases.get(field_name, field_name)
                used_by.setdefault(advertised, []).append(action)
                raw.setdefault(advertised, spec)

        for field_name, spec in raw.items():
            if is_shared(field_name):
                merged = dict(SHARED_FIELDS[field_name])
                if spec.get("description"):
                    merged["description"] = spec["description"]
                properties[field_name] = merged
                continue
            merged = dict(spec)
            text = spec.get("description", field_name)
            merged["description"] = (
                f"[{', '.join(used_by[field_name])}] {text}"
                if len(self.members) > 1
                else text
            )
            properties[field_name] = merged

        return {
            "type": "object",
            "properties": properties,
            "required": ["action"],
        }

    def help(self) -> dict[str, Any]:
        """Describe every member action and its fields."""
        actions: list[dict[str, Any]] = []
        for action, member in sorted(self.members.items()):
            aliases = self._aliases_for(member)
            props = {
                aliases.get(field_name, field_name): spec
                for field_name, spec in member.properties.items()
            }
            required = [
                aliases.get(field_name, field_name) for field_name in member.required
            ]
            actions.append(
                {
                    "action": action,
                    "description": member.description,
                    "required": required,
                    "optional": sorted(f for f in props if f not in required),
                    "fields": {
                        field_name: spec.get("description", "")
                        for field_name, spec in sorted(props.items())
                    },
                    "defaults": {
                        field_name: spec["default"]
                        for field_name, spec in sorted(props.items())
                        if "default" in spec
                    },
                }
            )
        return {
            "tool": self.name,
            "actions": actions,
        }

    def _rename_to_member_fields(
        self, member: MemberSpec, params: dict[str, Any]
    ) -> dict[str, Any]:
        aliases = self._aliases_for(member)
        if not aliases:
            return params
        renamed = dict(params)
        for own_field, alias in aliases.items():
            if alias in renamed:
                renamed[own_field] = renamed.pop(alias)
        return renamed

    def _clean_params(
        self, member: MemberSpec, params: dict[str, Any]
    ) -> dict[str, Any]:
        renamed = self._rename_to_member_fields(member, params)
        cleaned: dict[str, Any] = {}
        for key, value in renamed.items():
            if key not in member.properties:
                continue
            if _is_absent(value):
                continue
            cleaned[key] = value
        return cleaned

    def _tool_disabled_envelope(self, action: str) -> dict[str, str] | None:
        action_key = f"{self.name}.{action}"
        disabled = disabled_tools()
        if is_tool_disabled(action_key):
            tool = action_key if action_key in disabled else self.name
            return {"error": "tool_disabled", "tool": tool}
        return None

    def _missing_required(
        self, member: MemberSpec, cleaned: dict[str, Any]
    ) -> list[str]:
        aliases = self._aliases_for(member)
        missing: list[str] = []
        for field_name in member.required:
            advertised = aliases.get(field_name, field_name)
            if _is_absent(cleaned.get(field_name)):
                missing.append(advertised)
        return missing

    def execute(self, params: dict[str, Any]) -> Any:
        """Run the requested action after per-action validation and stripping."""
        params = dict(params)
        action = params.pop("action", None)

        if action == HELP_ACTION:
            if is_tool_disabled(self.name):
                return {"error": "tool_disabled", "tool": self.name}
            return self.help()

        if action not in self.members:
            return {
                "error": "action_unknown",
                "tool": self.name,
                "action": action,
                "hint": "help",
            }

        member = self.members[action]
        cleaned = self._clean_params(member, params)
        missing = self._missing_required(member, cleaned)
        if missing:
            return {
                "error": "action_args_invalid",
                "tool": self.name,
                "action": action,
                "required": missing,
            }

        disabled = self._tool_disabled_envelope(action)
        if disabled is not None:
            return disabled

        if member.rate_limit_key is not None:
            rate_err = check_rate_limit(member.rate_limit_key)
            if rate_err is not None:
                return rate_err

        return member.call(**cleaned)
