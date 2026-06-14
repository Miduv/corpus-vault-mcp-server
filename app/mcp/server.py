"""MCP (Model Context Protocol) stdio server for the Obsidian Vault Agent.

This server exposes a minimal set of tools for interacting with a mounted vault
via the existing `VaultService`, without going through the HTTP API.
"""

import os
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from app.mcp.oauth import (
    OAuthMiddleware,
    OAuthStore,
    create_authorization_endpoint,
    create_authorization_server_metadata_endpoint,
    create_dynamic_client_registration_endpoint,
    create_protected_resource_metadata_endpoint,
    create_token_endpoint,
)
from app.mcp.profiles import (
    READ_ONLY_MODE,
    WRITE_MODE,
    assert_no_public_write_tools,
    validate_startup_profile,
)
from app.vault.service import VaultService


def _access_mode() -> str:
    return os.getenv("MCP_ACCESS_MODE", READ_ONLY_MODE).strip().lower() or READ_ONLY_MODE


def _writes_allowed() -> bool:
    return _access_mode() == WRITE_MODE


def _registered_tool_names() -> list[str]:
    return list(mcp._tool_manager._tools)


def _validate_no_public_write_tools() -> None:
    assert_no_public_write_tools(_registered_tool_names())


def _sse_without_oauth_is_production_safe() -> bool:
    return False


# NOTE: For Cloudflare Quick Tunnel the public hostname changes often, which
# clashes with DNS rebinding protection allowlists. We'll disable it for now.
mcp = FastMCP(
    name="ObsidianVaultAgent",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _get_vault_service() -> VaultService:
    vault_path = os.getenv("VAULT_PATH", "/vault")
    if not Path(vault_path).exists():
        raise ValueError("vault path is not configured or does not exist")
    return VaultService(vault_path=vault_path)


def _safe_error_message(exc: Exception) -> str:
    # Avoid leaking absolute filesystem paths in error messages.
    if isinstance(exc, FileNotFoundError):
        return "path not found"
    if isinstance(exc, NotADirectoryError):
        return "path is not a directory"
    if isinstance(exc, IsADirectoryError):
        return "path is a directory"
    if isinstance(exc, ValueError):
        return str(exc)
    return "unexpected error"


@mcp.tool()
def vault_ls(path: str) -> dict[str, Any]:
    """List directories and markdown files inside the vault.

    Args:
        path: Relative path inside vault (empty string for root)
    """
    svc = _get_vault_service()
    try:
        return {"entries": svc.ls(path=path)}
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


@mcp.tool()
def vault_read(path: str) -> dict[str, str]:
    """Read a markdown file inside the vault and return its content."""
    svc = _get_vault_service()
    try:
        return {"content": svc.read(path=path)}
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


def _vault_write_controlled(path: str, content: str) -> dict[str, bool]:
    """Isolated write path for future controlled-write modes; not a public MCP tool."""
    if not _writes_allowed():
        raise PermissionError("write operations are disabled in read-only mode")

    svc = _get_vault_service()
    try:
        svc.write(path=path, content=content)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


@mcp.tool()
def vault_glob(pattern: str) -> dict[str, list[str]]:
    """Find markdown files and directories matching a glob pattern.

    This tool allows LLM to work with sets of files efficiently, enabling
    comparison, aggregation, and coverage analysis without multiple ls calls.

    Args:
        pattern: Glob pattern relative to vault root. Supports:
            - "Ежедневные/2025/**/*.md" - recursive search
            - "Дистилляция/Daily/2025-*.md" - date pattern matching
            - "**/*.md" - all markdown files recursively

    Returns:
        Dictionary with "files" and "dirs" lists of relative paths.
    """
    svc = _get_vault_service()
    try:
        return svc.glob(pattern=pattern)
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


@mcp.tool()
def vault_tree() -> dict[str, Any]:
    """Get the complete directory tree structure of the vault.

    This tool provides a hierarchical view of the entire vault structure,
    making it easy for LLM to understand the organization and navigate
    the knowledge base.

    Returns:
        Nested dictionary structure representing the vault tree.
        Root node has name "root", path "", and type "dir".
        Each directory node contains a "children" list with subdirectories
        and markdown files.
    """
    svc = _get_vault_service()
    try:
        return svc.tree()
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


@mcp.tool()
def vault_search(query: str, case_sensitive: bool = False) -> dict[str, Any]:
    """Search for text in all markdown files within the vault.

    This tool enables full-text search across the entire vault, allowing LLM
    to find relevant notes based on content, not just filenames.

    Args:
        query: Text to search for in file contents.
        case_sensitive: If True, search is case-sensitive (default: False).

    Returns:
        Dictionary with "matches" list and "total_files" count.
        Each match contains:
        - "path": relative path to the file
        - "line": line number (1-based) where match was found
        - "content": the line content containing the match
    """
    svc = _get_vault_service()
    try:
        return svc.search(query=query, case_sensitive=case_sensitive)
    except Exception as e:  # noqa: BLE001
        raise ValueError(_safe_error_message(e)) from None


if __name__ == "__main__":
    import sys

    profile = validate_startup_profile()
    _validate_no_public_write_tools()

    if profile.transport == "stdio":
        # stdio mode for direct MCP client connection
        mcp.run(transport="stdio")
    else:
        # SSE mode for network access (default for Docker)
        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_PORT", "8001"))
        use_oauth = profile.oauth_enabled

        # Create SSE app
        mcp_app = mcp.sse_app()

        if use_oauth:
            # ChatGPT MCP OAuth 2.1 with PKCE
            issuer = profile.oauth_issuer or os.getenv(
                "MCP_OAUTH_ISSUER", f"http://localhost:{port}"
            )
            static_client_id = os.getenv("MCP_OAUTH_CLIENT_ID")
            static_client_secret = os.getenv("MCP_OAUTH_CLIENT_SECRET")

            print(
                "OAuth 2.1 (Authorization Code + PKCE) for ChatGPT MCP", file=sys.stderr, flush=True
            )

            oauth_store = OAuthStore(
                allow_any_client=False,
                static_client_id=static_client_id,
                static_client_secret=static_client_secret,
            )

            # Resource URI for protected resource metadata
            resource_uri = issuer
            metadata_uri = f"{issuer}/.well-known/oauth-protected-resource"

            # Create main app with OAuth endpoints
            app = Starlette(
                routes=[
                    # Protected Resource Metadata (RFC 9728)
                    create_protected_resource_metadata_endpoint(resource_uri, issuer),
                    # Authorization Server Metadata (RFC 8414)
                    create_authorization_server_metadata_endpoint(issuer),
                    # Dynamic Client Registration (RFC 7591)
                    create_dynamic_client_registration_endpoint(oauth_store),
                    # Authorization endpoint (OAuth 2.1)
                    create_authorization_endpoint(oauth_store),
                    # Token endpoint
                    create_token_endpoint(oauth_store),
                    # MCP SSE endpoint
                    Mount("/", app=mcp_app),
                ],
                middleware=[
                    Middleware(
                        OAuthMiddleware,
                        oauth_store=oauth_store,
                        protected_paths=["/sse", "/messages"],
                        resource_uri=resource_uri,
                        metadata_uri=metadata_uri,
                    )
                ],
            )

            print(f"OAuth issuer: {issuer}", file=sys.stderr, flush=True)
            print(f"Protected Resource: {metadata_uri}", file=sys.stderr, flush=True)
            print(
                f"Authorization Server: {issuer}/.well-known/oauth-authorization-server",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Endpoints: /oauth/authorize, /oauth/token, /oauth/register",
                file=sys.stderr,
                flush=True,
            )
        else:
            # No authentication mode
            app = mcp_app
            if profile.warning:
                print("=" * 72, file=sys.stderr, flush=True)
                print(profile.warning, file=sys.stderr, flush=True)
                print("NOT PRODUCTION-SAFE", file=sys.stderr, flush=True)
                print("=" * 72, file=sys.stderr, flush=True)

        print(f"Starting MCP SSE server on {host}:{port}", file=sys.stderr, flush=True)
        uvicorn.run(app, host=host, port=port, log_level="info")
