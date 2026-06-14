from __future__ import annotations

from collections.abc import Iterable
from itertools import chain
from pathlib import Path
from typing import Any

from app.vault.path_policy import PathPolicy


class VaultService:
    """Service layer for interacting with an Obsidian vault on disk.

    This class will be used by the HTTP API layer.
    """

    def __init__(self, vault_path: str) -> None:
        self.vault_path = vault_path
        self._policy = PathPolicy(vault_path)

    def ls(self, path: str = "") -> list[dict[str, str]]:
        """List directories and markdown files inside the vault."""
        base = self._policy.base
        target = self._policy.validate_ls_path(path)
        return self._list_dir(base=base, target=target)

    def read(self, path: str) -> str:
        """Read a markdown file inside the vault and return its content."""
        if not path or not path.strip():
            raise ValueError("path must be non-empty")
        target = self._policy.validate_read_path(path)
        return target.read_text(encoding="utf-8")

    def write(self, path: str, content: str) -> None:
        """Write markdown content to a file inside the vault.

        Kept for future controlled-write modes; public MCP write access is disabled
        by default and must be gated before calling this method.
        """
        if not path or not path.strip():
            raise ValueError("path must be non-empty")
        target = self._policy.validate_write_path(path)
        if target.exists() and target.is_dir():
            raise IsADirectoryError(str(target))
        target.write_text(content, encoding="utf-8")

    def _resolve_inside_vault(self, path: str) -> tuple[Path, Path]:
        """Compatibility wrapper around the centralized path policy."""
        target = self._policy.validate_ls_path(path)
        return self._policy.base, target

    def _ensure_dir(self, path: Path) -> None:
        """Ensure `path` exists and is a directory.

        Raises:
        - FileNotFoundError: if the path does not exist.
        - NotADirectoryError: if the path exists but is not a directory.
        """
        if not path.exists():
            raise FileNotFoundError(str(path))
        if not path.is_dir():
            raise NotADirectoryError(str(path))

    def _ensure_file(self, path: Path) -> None:
        """Ensure `path` exists and is a file.

        Raises:
        - FileNotFoundError: if the path does not exist.
        - IsADirectoryError: if the path exists but is not a file.
        """
        if not path.exists():
            raise FileNotFoundError(str(path))
        if not path.is_file():
            raise IsADirectoryError(str(path))

    def _is_hidden(self, name: str) -> bool:
        """Return True if an entry name is considered hidden."""
        return name.startswith(".")

    def _is_markdown(self, path: Path) -> bool:
        """Return True if a filesystem path has a `.md` extension."""
        return path.suffix.lower() == ".md"

    def _to_item(self, base: Path, entry: Path, type_: str) -> dict[str, str]:
        """Convert a filesystem entry to a serializable list item.

        `path` in the result is always POSIX-like and relative to the vault root.
        """
        return {"type": type_, "name": entry.name, "path": entry.relative_to(base).as_posix()}

    def glob(self, pattern: str) -> dict[str, list[str]]:
        """Find files and directories matching a glob pattern.

        Rules:
        - `pattern` must be a relative glob pattern inside the vault.
        - Hidden entries (any path component starting with ".") are excluded.
        - Files are limited to `.md`. Directories are included as-is.
        - Results are returned as relative paths from vault root.

        Examples:
        - "Ежедневные/2025/**/*.md" - all markdown files in 2025 subdirectories
        - "Дистилляция/Daily/2025-*.md" - markdown files matching date pattern
        - "**/*.md" - all markdown files recursively

        Returns:
            Dictionary with "files" and "dirs" lists of relative paths.
        """
        pattern = self._policy.validate_glob_pattern(pattern)
        base = self._policy.base

        # Perform glob search from vault root
        files: list[str] = []
        dirs: list[str] = []
        matches: Iterable[Path]

        # For recursive patterns (**), split into base path and suffix pattern
        if "**" in pattern:
            # Split pattern at first **
            parts = pattern.split("**", 1)
            base_pattern = parts[0].rstrip("/")
            suffix_pattern = parts[1].lstrip("/") if len(parts) > 1 else ""

            if base_pattern:
                # Resolve base path and ensure it's inside vault
                base_dir = self._policy.validate_ls_path(base_pattern)

                # Use rglob with suffix pattern
                if suffix_pattern:
                    matches = base_dir.rglob(suffix_pattern)
                else:
                    # Pattern ends with **, match everything recursively
                    matches = chain([base_dir], base_dir.rglob("*"))
            else:
                # Pattern starts with **, search from vault root
                matches = base.rglob(suffix_pattern if suffix_pattern else "*")
        else:
            # Non-recursive pattern, use regular glob
            matches = base.glob(pattern)

        for match in matches:
            if not self._policy.is_allowed_existing_result(match):
                continue
            resolved_match = match.resolve(strict=True)
            posix_path = self._policy.relative_posix(resolved_match)

            if resolved_match.is_file():
                files.append(posix_path)
            elif resolved_match.is_dir():
                dirs.append(posix_path)

        # Sort for stable output
        return {
            "files": sorted(files),
            "dirs": sorted(dirs),
        }

    def _list_dir(self, *, base: Path, target: Path) -> list[dict[str, str]]:
        """List non-hidden subdirectories and markdown files in `target`.

        - Directories are included as-is.
        - Files are included only when they have a `.md` extension.
        - Entries are sorted by case-insensitive name for stable output.
        """
        items: list[dict[str, str]] = []

        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not self._policy.is_allowed_existing_result(entry):
                continue

            resolved_entry = entry.resolve(strict=True)
            if resolved_entry.is_dir():
                items.append(self._to_item(base, resolved_entry, "dir"))
                continue

            if resolved_entry.is_file():
                items.append(self._to_item(base, resolved_entry, "file"))

        return items

    def tree(self) -> dict[str, Any]:
        """Get the complete directory tree of the vault.

        Rules:
        - Returns nested structure starting from vault root.
        - Hidden entries (starting with ".") are excluded.
        - Files are limited to `.md`. Directories are included as-is.
        - Results are sorted by name (case-insensitive).

        Returns:
            Nested dictionary structure with "name", "path", "type", and "children" keys.
            Root node has name "root", path "", and type "dir".
        """
        base = self._policy.validate_tree_root()
        return self._build_tree(base, base, "")

    def _build_tree(self, base: Path, current: Path, relative_path: str) -> dict[str, Any]:
        """Recursively build tree structure for a directory.

        Args:
            base: Absolute path to vault root.
            current: Current directory being processed.
            relative_path: Relative path from vault root to current directory.

        Returns:
            Dictionary with tree structure for current directory.
        """
        children: list[dict[str, Any]] = []

        # Get all entries in current directory
        entries = sorted(current.iterdir(), key=lambda p: p.name.lower())

        for entry in entries:
            if not self._policy.is_allowed_existing_result(entry):
                continue

            resolved_entry = entry.resolve(strict=True)
            entry_relative = self._policy.relative_posix(resolved_entry)

            if resolved_entry.is_dir():
                # Recursively build tree for subdirectory
                child_tree = self._build_tree(base, resolved_entry, entry_relative)
                children.append(child_tree)
            elif resolved_entry.is_file():
                # Add markdown file
                children.append(
                    {
                        "name": resolved_entry.name,
                        "path": entry_relative,
                        "type": "file",
                    }
                )

        # Build current node
        node: dict[str, Any] = {
            "name": current.name if relative_path else "root",
            "path": relative_path,
            "type": "dir",
        }

        if children:
            node["children"] = children

        return node

    def search(self, query: str, case_sensitive: bool = False) -> dict[str, Any]:
        """Search for text in all markdown files within the vault.

        Rules:
        - Searches recursively through all `.md` files.
        - Hidden files and directories are excluded.
        - Returns matches with file path, line number, and line content.
        - Results are sorted by file path and line number.

        Args:
            query: Text to search for.
            case_sensitive: If True, search is case-sensitive (default: False).

        Returns:
            Dictionary with "matches" list and "total_files" count.
            Each match contains "path", "line" (1-based), and "content".
        """
        if not query or not query.strip():
            raise ValueError("query must be non-empty")

        base = self._policy.validate_search_root()
        matches: list[dict[str, Any]] = []
        processed_files = 0

        # Recursively find all markdown files
        for file_path in base.rglob("*.md"):
            if not self._policy.is_allowed_existing_result(file_path, allow_dir=False):
                continue
            resolved_file_path = file_path.resolve(strict=True)
            relative_path = resolved_file_path.relative_to(base)

            try:
                # Read file content
                content = resolved_file_path.read_text(encoding="utf-8")
                lines = content.splitlines()
                processed_files += 1

                # Search in each line
                for line_num, line in enumerate(lines, start=1):
                    if case_sensitive:
                        found = query in line
                    else:
                        found = query.lower() in line.lower()

                    if found:
                        matches.append(
                            {
                                "path": relative_path.as_posix(),
                                "line": line_num,
                                "content": line.strip(),
                            }
                        )
            except (UnicodeDecodeError, PermissionError):
                # Skip files that can't be read
                continue

        # Sort by path and line number
        matches.sort(key=lambda m: (m["path"], m["line"]))

        return {
            "matches": matches,
            "total_files": processed_files,
        }

