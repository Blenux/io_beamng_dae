"""Pure-Python replacement for the cdae_native C++ module.

Exposes the same API as the original pybind11 module:
    parse_dae(filepath) → dict
    parse_dae_bytes(data) → dict
    write_dae(filepath, data, authoring_tool="Blender") → bool
    parse_cdae(filepath) → dict
    parse_cdae_bytes(data) → dict
    write_cdae(filepath, data, compress=False) → bool
"""

__version__ = "2.0.0"

from .dae_parser import parse_dae, parse_dae_bytes
from .dae_writer import write_dae
from .cdae_parser import parse_cdae, parse_cdae_bytes
from .cdae_writer import write_cdae

__all__ = [
    "parse_dae",
    "parse_dae_bytes",
    "write_dae",
    "parse_cdae",
    "parse_cdae_bytes",
    "write_cdae",
    "__version__",
]
