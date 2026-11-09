"""Pure-Python materials.json (SJSON) parse/write backend — the
io_beamng_dae_python counterpart of the dev repo's cdae_native entry points.

Backed by the vendored beamng_materials._sjson parser (comments, trailing
commas, unquoted keys tolerated).
"""

from ..vendor.beamng_materials import _sjson


def parse_materials_json(path):
    """Parse a *.materials.json file → {name: material_dict}.
    Returns {} on missing/parse error."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return _sjson.parse(f.read()) or {}
    except Exception:
        return {}


def write_materials_json(path, materials):
    """Serialize {name: material_dict} to a .materials.json file.
    Returns True on success."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_sjson.write(materials))
        return True
    except Exception:
        return False
