"""Texture path, suffix and colour-space utilities."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


# BeamNG colour texture suffixes. Anything else is treated as Non-Color data.
_SRGB_SUFFIXES = (
    "_b.color", "_d.color", "_s.color", "_g.color",
    "_p.color", "_c.color", "_r.color", "_d.dds", "_c.dds",
)


def is_srgb_texture(path: str) -> bool:
    """Return True if the texture path denotes an sRGB colour texture.

    Mirrors the fork's ``is_srgb_texture`` logic.
    """
    basename = os.path.basename(path).lower()
    for ext in (".dds", ".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr"):
        if basename.endswith(ext):
            stem = basename[: -len(ext)]
            for suffix in _SRGB_SUFFIXES:
                if stem.endswith(suffix):
                    return True
    return False


def _resolve_case_insensitive(base: Path, parts: List[str]) -> Optional[Path]:
    """Walk a relative path under ``base`` matching each component case-insensitively."""
    current = base
    for part in parts:
        if not current.is_dir():
            return None
        if not part or part == ".":
            continue
        lower = part.casefold()
        found: Optional[Path] = None
        for child in current.iterdir():
            if child.name.casefold() == lower:
                found = child
                break
        if found is None:
            return None
        current = found
    return current if current.is_file() else None


@dataclass
class TextureResolver:
    """Resolve relative texture paths against one or more base directories.

    BeamNG stores image paths relative to the game root.  The resolver tries
    the original path, then a ``.png`` -> ``.dds`` fallback (and vice-versa).
    """

    base_dirs: list[Path] = field(default_factory=list)

    def add_base_dir(self, path: str | Path) -> None:
        self.base_dirs.append(Path(path))

    def resolve(self, tex_path: str | None) -> Optional[Path]:
        if not tex_path:
            return None
        tex_path = tex_path.replace("\\", "/").lstrip("/")

        candidates = [tex_path]
        lower = tex_path.lower()
        has_ext = any(lower.endswith(ext) for ext in (".png", ".dds", ".jpg"))

        if has_ext:
            for ext in (".png", ".dds", ".jpg"):
                if lower.endswith(ext):
                    base = tex_path[: -len(ext)]
                    for other in (".png", ".dds", ".jpg"):
                        if other != ext:
                            candidates.append(base + other)
                    break
        else:
            for ext in (".dds", ".png", ".jpg"):
                candidates.append(tex_path + ext)

        for base in self.base_dirs:
            for cand in candidates:
                p = base / cand
                if p.is_file():
                    return p
                # BeamNG file references are case-insensitive (Windows-style).
                parts = [part for part in cand.split("/") if part]
                ci = _resolve_case_insensitive(base, parts)
                if ci:
                    return ci
        return None


def resolve_texture_path(tex_path: str, base_dirs: Iterable[str | Path]) -> Optional[Path]:
    """Convenience one-shot resolver."""
    resolver = TextureResolver([Path(p) for p in base_dirs])
    return resolver.resolve(tex_path)
