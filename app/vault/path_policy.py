"""Centralized path safety policy for vault filesystem access."""

from __future__ import annotations

from pathlib import Path

HIDDEN_DIR_DENYLIST = {".git", ".obsidian", ".env", ".ssh"}


class PathPolicyError(ValueError):
    """Raised when a caller-supplied vault path violates policy."""


class PathPolicy:
    """Validate and resolve caller-supplied paths inside a vault root."""

    def __init__(self, vault_path: str | Path) -> None:
        self.base = Path(vault_path).resolve(strict=True)

    def validate_read_path(self, path: str) -> Path:
        target = self._resolve_existing(path, require_markdown=True)
        if not target.is_file():
            raise IsADirectoryError(str(target))
        return target

    def validate_write_path(self, path: str) -> Path:
        relative = self._validate_relative_parts(path)
        if relative.suffix.lower() != ".md":
            raise PathPolicyError("only .md files are allowed")

        parent = self._resolve_existing_parent(relative)
        return parent / relative.name

    def validate_ls_path(self, path: str = "") -> Path:
        target = self._resolve_existing(path or "")
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        return target

    def validate_tree_root(self) -> Path:
        return self.base

    def validate_glob_pattern(self, pattern: str) -> str:
        if not pattern or not pattern.strip():
            raise PathPolicyError("pattern must be non-empty")
        try:
            self._validate_relative_parts(pattern, allow_glob=True)
        except PathPolicyError as exc:
            if "absolute paths" in str(exc) or "parent path" in str(exc):
                raise PathPolicyError("pattern must be relative and not escape vault") from exc
            raise
        return pattern

    def validate_search_root(self) -> Path:
        return self.base

    def relative_posix(self, path: Path) -> str:
        return path.relative_to(self.base).as_posix()

    def is_allowed_existing_result(self, path: Path, *, allow_dir: bool = True) -> bool:
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(self.base)
        except (OSError, ValueError):
            return False
        if self._has_hidden_segment(relative.parts):
            return False
        if resolved.is_dir():
            return allow_dir
        return resolved.is_file() and resolved.suffix.lower() == ".md"

    def _resolve_existing(self, path: str, *, require_markdown: bool = False) -> Path:
        relative = self._validate_relative_parts(path)
        target = (self.base / relative).resolve(strict=True)
        self._ensure_inside(target)
        if require_markdown and target.suffix.lower() != ".md":
            raise PathPolicyError("only .md files are allowed")
        return target

    def _resolve_existing_parent(self, relative: Path) -> Path:
        parent = (self.base / relative.parent).resolve(strict=True)
        self._ensure_inside(parent)
        if not parent.is_dir():
            raise NotADirectoryError(str(parent))
        return parent

    def _validate_relative_parts(self, path: str, *, allow_glob: bool = False) -> Path:
        if path is None or not str(path).strip():
            if allow_glob:
                raise PathPolicyError("pattern must be non-empty")
            return Path("")

        relative = Path(path)
        if relative.is_absolute():
            raise PathPolicyError("absolute paths are not allowed")
        if ".." in relative.parts:
            raise PathPolicyError("parent path segments are not allowed")

        parts = [part for part in relative.parts if part not in ("", ".")]
        if self._has_hidden_segment(parts):
            raise PathPolicyError("hidden paths are not allowed")
        return relative

    def _has_hidden_segment(self, parts: tuple[str, ...] | list[str]) -> bool:
        for part in parts:
            if part in ("", "."):
                continue
            if part in HIDDEN_DIR_DENYLIST or part.startswith("."):
                return True
        return False

    def _ensure_inside(self, path: Path) -> None:
        try:
            path.relative_to(self.base)
        except ValueError as exc:
            raise PathPolicyError("path escapes vault root") from exc
