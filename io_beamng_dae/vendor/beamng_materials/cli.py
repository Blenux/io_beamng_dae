"""Command-line interface for BeamNG material tools."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import _sjson as sjson
from .cs import load_cs, save_cs
from .io import load_library, save_library
from .material import Material, MaterialLibrary
from .terrain import TerrainLayer, TerrainMaterial
from .textures import TextureResolver


def load_any(path: str | Path) -> MaterialLibrary:
    """Load a material file regardless of whether it is ``.json`` or ``.cs``."""
    path = Path(path)
    if path.suffix == ".cs":
        return load_cs(path)
    return load_library(path)


def _is_texture_candidate(key: str, value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if key in {"name", "mapTo", "map_to", "class", "class_name", "internalName",
               "translucentBlendOp", "groundType", "annotation", "cubemap",
               "annotationMap", "materialTag0", "materialTag1", "materialTag2"}:
        return False
    if "/" in value or "\\" in value or ".dds" in value.lower() or ".png" in value.lower():
        return True
    return False


def _collect_strings(obj: Any, seen: Optional[Set[int]] = None) -> Iterable[Tuple[str, str]]:
    """Recursively yield (attribute/key, string) pairs from a dataclass."""
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and _is_texture_candidate(k, v):
                yield k, v
            elif isinstance(v, (dict, list)):
                yield from _collect_strings(v, seen)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _collect_strings(item, seen)
    elif hasattr(obj, "__dict__"):
        for k, v in obj.__dict__.items():
            if k in ("extra", "metadata"):
                continue
            if isinstance(v, str) and _is_texture_candidate(k, v):
                yield k, v
            elif isinstance(v, (dict, list)) or hasattr(v, "__dict__"):
                yield from _collect_strings(v, seen)
        if hasattr(obj, "extra"):
            for k, v in obj.extra.items():
                if isinstance(v, str) and _is_texture_candidate(k, v):
                    yield k, v
                elif isinstance(v, (dict, list)):
                    yield from _collect_strings(v, seen)


def collect_texture_references(library: MaterialLibrary) -> Set[Tuple[str, str, str]]:
    """Return (material_name, key, path) for every texture-like string in a library."""
    refs: Set[Tuple[str, str, str]] = set()
    for name, mat in library.materials.items():
        for k, v in _collect_strings(mat):
            refs.add((name, k, v))
    return refs


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a material file."""
    lib = load_any(args.path)
    print(f"Loaded {len(lib)} material(s) from {args.path}")

    issues: List[str] = []
    for name, mat in lib.materials.items():
        if not name:
            issues.append("Unnamed material")
        if getattr(mat, "version", None) is None:
            issues.append(f"{name}: version is not set")
        if getattr(mat, "map_to", None) is None:
            issues.append(f"{name}: mapTo is not set")
        if mat.extra:
            for k in sorted(mat.extra):
                issues.append(f"{name}: unknown top-level key '{k}'")
        if hasattr(mat, "stages"):
            for i, stage in enumerate(mat.stages):
                if stage.extra:
                    for k in sorted(stage.extra):
                        issues.append(f"{name}: unknown stage[{i}] key '{k}'")

    if args.base_dir:
        resolver = TextureResolver([Path(p) for p in args.base_dir])
        refs = collect_texture_references(lib)
        missing = [(m, k, v) for m, k, v in refs if resolver.resolve(v) is None]
        for m, k, v in sorted(missing):
            issues.append(f"{m}: missing texture '{v}' (key: {k})")

    if issues:
        print("Issues found:")
        for i in issues:
            print(f"  - {i}")
        return 1

    print("No issues found.")
    return 0


def _set_version(mat: Material, version: Optional[str]) -> None:
    if version is None:
        return
    from .enums import MaterialVersion
    if version == "1.0":
        mat.version = MaterialVersion.V1
    elif version == "1.5":
        mat.version = MaterialVersion.V1_5
    else:
        raise ValueError(f"Unknown version {version!r}")


def cmd_migrate(args: argparse.Namespace) -> int:
    """Convert a material file to another format or version."""
    lib = load_any(args.path)
    for mat in lib.materials.values():
        if isinstance(mat, Material):
            _set_version(mat, args.to_version)

    out = Path(args.output)
    if args.format == "cs":
        save_cs(lib, out)
    else:
        save_library(lib, out, indent=args.indent)

    print(f"Wrote {len(lib)} material(s) to {out}")
    return 0


def cmd_textures(args: argparse.Namespace) -> int:
    """List and optionally check texture references."""
    lib = load_any(args.path)
    refs = collect_texture_references(lib)
    print(f"Found {len(refs)} texture reference(s):")

    missing: List[Tuple[str, str, str]] = []
    resolver = TextureResolver([Path(p) for p in args.base_dir]) if args.base_dir else None

    for mat, key, path in sorted(refs):
        status = ""
        if resolver:
            resolved = resolver.resolve(path)
            if resolved is None:
                missing.append((mat, key, path))
                status = " MISSING"
            else:
                status = f" OK ({resolved})"
        print(f"  {mat}.{key}: {path}{status}")

    if missing:
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Print a short summary of a material file."""
    lib = load_any(args.path)
    print(f"File: {args.path}")
    print(f"Materials: {len(lib)}")
    for name, mat in sorted(lib.materials.items()):
        kind = type(mat).__name__
        print(f"  {name} ({kind})")
        if hasattr(mat, "stages"):
            active = sum(1 for s in mat.stages if not s.is_empty())
            print(f"    active stages: {active}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beamng-materials",
        description="Inspect, validate and convert BeamNG material libraries.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate a .json or .cs material file.")
    p_validate.add_argument("path", help="Input material file.")
    p_validate.add_argument("--base-dir", action="append", default=[], help="Base directory for texture lookup.")

    p_migrate = sub.add_parser("migrate", help="Convert between .json and .cs or change version.")
    p_migrate.add_argument("path", help="Input material file.")
    p_migrate.add_argument("-o", "--output", required=True, help="Output file.")
    p_migrate.add_argument("--format", choices=["json", "cs"], default="json", help="Output format.")
    p_migrate.add_argument("--to-version", choices=["1.0", "1.5"], help="Set material version.")
    p_migrate.add_argument("--indent", type=int, default=2, help="JSON indentation.")

    p_textures = sub.add_parser("textures", help="List texture references and optionally check existence.")
    p_textures.add_argument("path", help="Input material file.")
    p_textures.add_argument("--base-dir", action="append", default=[], required=True, help="Base directory for texture lookup.")

    p_info = sub.add_parser("info", help="Show a summary of a material file.")
    p_info.add_argument("path", help="Input material file.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "migrate":
        return cmd_migrate(args)
    if args.command == "textures":
        return cmd_textures(args)
    if args.command == "info":
        return cmd_info(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
