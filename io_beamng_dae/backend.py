"""Per-repo backend shim — the ONLY io_beamng_dae file allowed to differ
between io_beamng_dae_dev (cdae_native C++ extension) and
io_beamng_dae_python (pure-Python native/ package).

parse_materials_json() / write_materials_json() take and return plain dicts;
everything bpy-facing lives in the shared layer.

The vendored _sjson fallback is loaded by file path so this module also
works standalone (importing io_beamng_dae.* requires bpy).
"""

import os
import importlib.util


def _native_materials_json():
    """native/materials_json.py — the pure-Python backend."""
    try:
        from .native import materials_json
        return materials_json
    except Exception:
        return None


def _load_sjson_module():
    """Load vendor/beamng_materials/_sjson.py directly (no package import)."""
    path = os.path.join(os.path.dirname(__file__),
                        "vendor", "beamng_materials", "_sjson.py")
    spec = importlib.util.spec_from_file_location("bng_sjson", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_materials_json(path):
    """Parse a *.materials.json file (SJSON: comments + trailing commas
    tolerated). Returns {name: material_dict} — {} on missing/parse error."""
    backend = _native_materials_json()
    if backend is not None:
        return backend.parse_materials_json(path)

    try:
        sjson = _load_sjson_module()
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return sjson.parse(f.read()) or {}
    except Exception:
        return {}


def write_materials_json(path, materials):
    """Serialize {name: material_dict} to a .materials.json file.
    Returns True on success."""
    backend = _native_materials_json()
    if backend is not None:
        return backend.write_materials_json(path, materials)

    try:
        sjson = _load_sjson_module()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(sjson.write(materials))
        return True
    except Exception:
        return False
