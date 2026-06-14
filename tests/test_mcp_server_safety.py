"""Safety tests for the MCP server entrypoint."""

from __future__ import annotations

import pytest

from app.mcp import server


def test_write_tool_unavailable_in_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public vault_write tool is not exported and controlled write refuses READ_ONLY."""
    monkeypatch.delenv("MCP_ACCESS_MODE", raising=False)

    assert not hasattr(server, "vault_write")
    assert server._access_mode() == server.READ_ONLY_MODE
    with pytest.raises(PermissionError, match="read-only mode"):
        server._vault_write_controlled("note.md", "content")


def test_server_defaults_to_stdio_read_only_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Server configuration supports stdio/read-only mode without enabling writes."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("MCP_ACCESS_MODE", raising=False)

    svc = server._get_vault_service()

    assert svc.vault_path == str(tmp_path)
    assert server._access_mode() == server.READ_ONLY_MODE
    assert not server._writes_allowed()


def test_sse_without_oauth_not_production_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE with OAuth disabled must not be treated as production-safe."""
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    monkeypatch.setenv("MCP_OAUTH_ENABLED", "false")

    assert not server._sse_without_oauth_is_production_safe()
