"""Startup profile validation tests."""

from __future__ import annotations

import pytest

from app.mcp import server
from app.mcp.profiles import validate_startup_profile


def test_local_stdio_readonly_profile_passes() -> None:
    profile = validate_startup_profile(
        {
            "MCP_PROFILE": "local-stdio-readonly",
            "MCP_TRANSPORT": "stdio",
            "MCP_ACCESS_MODE": "read_only",
        }
    )

    assert profile.name == "local-stdio-readonly"
    assert profile.transport == "stdio"
    assert profile.access_mode == "read_only"
    assert profile.production_safe
    assert not profile.writes_allowed


def test_sse_dev_without_oauth_passes_with_not_production_safe_warning() -> None:
    profile = validate_startup_profile(
        {
            "MCP_PROFILE": "sse-dev",
            "MCP_TRANSPORT": "sse",
            "MCP_ACCESS_MODE": "read_only",
            "MCP_OAUTH_ENABLED": "false",
        }
    )

    assert profile.name == "sse-dev"
    assert not profile.oauth_enabled
    assert not profile.production_safe
    assert profile.warning is not None
    assert "not production-safe" in profile.warning


def test_sse_without_oauth_without_sse_dev_fails() -> None:
    with pytest.raises(ValueError, match="sse-oauth requires MCP_OAUTH_ENABLED=true"):
        validate_startup_profile(
            {
                "MCP_PROFILE": "sse-oauth",
                "MCP_TRANSPORT": "sse",
                "MCP_ACCESS_MODE": "read_only",
                "MCP_OAUTH_ENABLED": "false",
            }
        )


def test_sse_oauth_without_oauth_configuration_fails() -> None:
    with pytest.raises(ValueError, match="sse-oauth requires MCP_OAUTH_ISSUER"):
        validate_startup_profile(
            {
                "MCP_PROFILE": "sse-oauth",
                "MCP_TRANSPORT": "sse",
                "MCP_ACCESS_MODE": "read_only",
                "MCP_OAUTH_ENABLED": "true",
            }
        )


def test_unknown_profile_fails() -> None:
    with pytest.raises(ValueError, match="unknown MCP_PROFILE"):
        validate_startup_profile({"MCP_PROFILE": "unsafe-sse"})


def test_read_only_profile_has_no_public_write_or_apply_tools() -> None:
    validate_startup_profile(
        {
            "MCP_PROFILE": "local-stdio-readonly",
            "MCP_TRANSPORT": "stdio",
            "MCP_ACCESS_MODE": "read_only",
        }
    )

    tool_names = server._registered_tool_names()

    assert tool_names
    assert not [name for name in tool_names if "write" in name or "apply" in name]
    server._validate_no_public_write_tools()
