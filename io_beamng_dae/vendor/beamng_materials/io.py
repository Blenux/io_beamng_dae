"""Load and save BeamNG material libraries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from . import _sjson as sjson
from .material import MaterialLibrary


def load_json(path: str | Path) -> Dict[str, Any]:
    """Read a ``main.materials.json`` file and return the raw dictionary.

    Uses the vendored SJSON parser so that comments, trailing commas, ``=``
    separators and other BeamNG SJSON extensions are handled exactly as in
    ``*.jbeam`` / ``*.pc`` files.  Falls back to plain JSON if the input is
    already strict JSON.
    """
    text = Path(path).read_text(encoding="utf-8")
    return sjson.parse(text)


def save_json(data: Dict[str, Any], path: str | Path, indent: int = 2) -> None:
    """Write a ``main.materials.json`` file from a raw dictionary."""
    Path(path).write_text(sjson.write(data, indent=indent), encoding="utf-8")


def load_library(path: str | Path) -> MaterialLibrary:
    """Load a ``main.materials.json`` file into a :class:`MaterialLibrary`."""
    return MaterialLibrary.from_dict(load_json(path))


def save_library(library: MaterialLibrary, path: str | Path, indent: int = 2) -> None:
    """Save a :class:`MaterialLibrary` to a ``main.materials.json`` file."""
    save_json(library.to_dict(), path, indent=indent)
