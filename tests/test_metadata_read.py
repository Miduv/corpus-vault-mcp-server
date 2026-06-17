from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.vault.service import VaultService

if TYPE_CHECKING:
    from pytest import TempPathFactory


@pytest.fixture
def vault_path(tmp_path_factory: TempPathFactory) -> Path:
    vault = tmp_path_factory.mktemp("metadata-vault")
    (vault / "note.md").write_text("# Test Note\n\nContent here.", encoding="utf-8")
    (vault / "metadata.md").write_text(
        "---\n"
        "title: Metadata Note\n"
        "tags: [mcp, vault]\n"
        "draft: false\n"
        "aliases:\n"
        "  - Meta\n"
        "---\n"
        "# Metadata Note\n"
        "\n"
        "Content words here.",
        encoding="utf-8",
    )
    (vault / ".hidden").mkdir()
    (vault / ".hidden" / "secret.md").write_text("Hidden content", encoding="utf-8")
    (vault / "not-markdown.txt").write_text("Not markdown", encoding="utf-8")
    return vault


@pytest.fixture
def service(vault_path: Path) -> VaultService:
    return VaultService(vault_path=str(vault_path))


def test_metadata_read_basic_stats_and_headings(service: VaultService) -> None:
    metadata = service.metadata_read("note.md")

    assert metadata["path"] == "note.md"
    assert metadata["name"] == "note.md"
    assert metadata["has_frontmatter"] is False
    assert metadata["frontmatter"] == {}
    assert metadata["headings"] == [{"level": 1, "text": "Test Note", "line": 1}]
    assert metadata["stats"]["line_count"] == 3
    assert metadata["stats"]["word_count"] == 5
    assert "content" not in metadata


def test_metadata_read_frontmatter(service: VaultService) -> None:
    metadata = service.metadata_read("metadata.md")

    assert metadata["has_frontmatter"] is True
    assert metadata["frontmatter"] == {
        "title": "Metadata Note",
        "tags": ["mcp", "vault"],
        "draft": False,
        "aliases": ["Meta"],
    }
    assert metadata["headings"] == [{"level": 1, "text": "Metadata Note", "line": 8}]


def test_metadata_read_hidden_path_raises(service: VaultService) -> None:
    with pytest.raises(ValueError, match="hidden paths are not allowed"):
        service.metadata_read(".hidden/secret.md")


def test_metadata_read_non_markdown_raises(service: VaultService) -> None:
    with pytest.raises(ValueError, match="only .md files are allowed"):
        service.metadata_read("not-markdown.txt")


def test_metadata_read_parent_path_blocked(service: VaultService) -> None:
    with pytest.raises(ValueError, match="parent path segments are not allowed"):
        service.metadata_read("../outside.md")
