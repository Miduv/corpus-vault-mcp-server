"""Startup profile validation for the MCP server.

The profile layer is intentionally fail-closed: unknown or incomplete network
configurations raise before the server starts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

LOCAL_STDIO_READONLY = "local-stdio-readonly"
SSE_DEV = "sse-dev"
SSE_OAUTH = "sse-oauth"

READ_ONLY_MODE = "read_only"
WRITE_MODE = "write"
STDIO_TRANSPORT = "stdio"
SSE_TRANSPORT = "sse"

_PROFILES = {LOCAL_STDIO_READONLY, SSE_DEV, SSE_OAUTH}
_WRITE_TOOL_NAME_PARTS = ("write", "apply")


@dataclass(frozen=True)
class StartupProfile:
    """Validated MCP startup profile."""

    name: str
    transport: str
    access_mode: str
    oauth_enabled: bool
    production_safe: bool
    warning: str | None = None
    oauth_issuer: str | None = None

    @property
    def writes_allowed(self) -> bool:
        """Return whether the private controlled-write path may run."""
        return self.access_mode == WRITE_MODE


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _get(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, default)
    return value.strip().lower() or default


def _oauth_enabled(env: Mapping[str, str]) -> bool:
    return _get(env, "MCP_OAUTH_ENABLED", "false") == "true"


def validate_startup_profile(env: Mapping[str, str] | None = None) -> StartupProfile:
    """Validate environment and return the explicit startup profile.

    Raises:
        ValueError: if the selected profile is unknown, contradictory, or unsafe.
    """
    values = _env(env)
    profile = _get(values, "MCP_PROFILE", LOCAL_STDIO_READONLY)
    transport = _get(values, "MCP_TRANSPORT", STDIO_TRANSPORT)
    access_mode = _get(values, "MCP_ACCESS_MODE", READ_ONLY_MODE)
    oauth_enabled = _oauth_enabled(values)

    if profile not in _PROFILES:
        raise ValueError(f"unknown MCP_PROFILE: {profile}")

    if access_mode == WRITE_MODE:
        # Controlled write is intentionally not part of this patch. The private
        # write helper remains guarded and no public write/apply tools are exported.
        access_mode = WRITE_MODE
    elif access_mode != READ_ONLY_MODE:
        raise ValueError(f"unsupported MCP_ACCESS_MODE: {access_mode}")

    if profile == LOCAL_STDIO_READONLY:
        if transport != STDIO_TRANSPORT:
            raise ValueError("local-stdio-readonly requires MCP_TRANSPORT=stdio")
        if access_mode != READ_ONLY_MODE:
            raise ValueError("local-stdio-readonly requires MCP_ACCESS_MODE=read_only")
        return StartupProfile(profile, transport, access_mode, oauth_enabled, True)

    if profile == SSE_DEV:
        if transport != SSE_TRANSPORT:
            raise ValueError("sse-dev requires MCP_TRANSPORT=sse")
        if access_mode != READ_ONLY_MODE:
            raise ValueError("sse-dev requires MCP_ACCESS_MODE=read_only")
        if oauth_enabled:
            raise ValueError("sse-dev requires MCP_OAUTH_ENABLED=false")
        return StartupProfile(
            profile,
            transport,
            access_mode,
            oauth_enabled,
            False,
            warning="WARNING: sse-dev has OAuth disabled and is not production-safe",
        )

    if profile == SSE_OAUTH:
        if transport != SSE_TRANSPORT:
            raise ValueError("sse-oauth requires MCP_TRANSPORT=sse")
        if not oauth_enabled:
            raise ValueError("sse-oauth requires MCP_OAUTH_ENABLED=true")
        issuer = values.get("MCP_OAUTH_ISSUER", "").strip()
        if not issuer:
            raise ValueError("sse-oauth requires MCP_OAUTH_ISSUER")
        return StartupProfile(
            profile, transport, access_mode, oauth_enabled, True, oauth_issuer=issuer
        )

    raise ValueError(f"unknown MCP_PROFILE: {profile}")


def assert_no_public_write_tools(tool_names: list[str]) -> None:
    """Reject public write/apply tools in read-only profiles."""
    blocked = [
        name for name in tool_names if any(part in name.lower() for part in _WRITE_TOOL_NAME_PARTS)
    ]
    if blocked:
        raise ValueError(f"public write/apply tools are disabled: {', '.join(blocked)}")
