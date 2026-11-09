"""Runtime material metadata extracted from JBeam vehicle files.

This module links the static material definitions to vehicle runtime behaviour:
``glowMap`` (light states / electrics), ``deformGroup`` (damage material
switching) and the electrics variables that drive them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import _sjson as sjson
from .material import Material


@dataclass
class GlowMapEntry:
    """One entry from a vehicle ``glowMap``."""

    org_mat: str
    off: Optional[str] = None
    on: Optional[str] = None
    on_intense: Optional[str] = None
    simple_function: Optional[Any] = None
    advanced_function: Optional[Dict[str, Any]] = None
    material_emissive_scaling: Optional[Dict[str, Any]] = None
    limit: Optional[float] = None

    @property
    def electrics_names(self) -> Set[str]:
        """Return the electrics variable names used by this glow map."""
        names: Set[str] = set()
        if isinstance(self.simple_function, str):
            names.add(self.simple_function)
        elif isinstance(self.simple_function, dict):
            names.update(self.simple_function.keys())
        if self.advanced_function:
            names.update(self.advanced_function.get("triggers", []))
            cmd = self.advanced_function.get("cmd", "")
            if isinstance(cmd, str):
                names.update(re.findall(r"electrics?\.([A-Za-z_][A-Za-z0-9_]*)", cmd))
        return names


@dataclass
class DeformGroupEntry:
    """Damage-driven material switch data from ``deformGroup`` definitions."""

    name: str
    damaged_material: Optional[str] = None
    trigger_ratios: List[float] = field(default_factory=list)
    beam_count: int = 0


@dataclass
class MaterialRuntimeMetadata:
    """Metadata that can be attached to a single :class:`Material`."""

    glow_map: Optional[GlowMapEntry] = None
    deform_groups: List[DeformGroupEntry] = field(default_factory=list)
    electrics: Set[str] = field(default_factory=set)


@dataclass
class VehicleMaterialMetadata:
    """All runtime material links found in one vehicle JBeam file."""

    glow_maps: Dict[str, GlowMapEntry] = field(default_factory=dict)
    deform_groups: Dict[str, DeformGroupEntry] = field(default_factory=dict)
    electrics: Set[str] = field(default_factory=set)

    @classmethod
    def from_jbeam(cls, data: Dict[str, Any]) -> "VehicleMaterialMetadata":
        """Parse a loaded JBeam vehicle dictionary.

        ``.jbeam`` files vary in layout: the vehicle object may appear directly,
        wrapped in a single top-level key, or split into several top-level part
        objects.  We collect every object that looks like a part and merge their
        runtime material metadata.
        """
        if isinstance(data, dict) and len(data) == 1:
            first = next(iter(data.values()))
            if isinstance(first, dict):
                data = first

        meta = cls()

        # Identify candidate part objects.  A part usually has flexbodies, beams,
        # nodes, slots, etc.  We include the top-level dict itself if it already
        # contains the keys we care about.
        candidates: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            if any(k in data for k in ("glowMap", "flexbodies", "beams")):
                candidates.append(data)
            for value in data.values():
                if isinstance(value, dict) and any(k in value for k in ("glowMap", "flexbodies", "beams", "slotType")):
                    candidates.append(value)

        for obj in candidates:
            cls._from_jbeam_object(meta, obj)

        return meta

    @classmethod
    def _from_jbeam_object(cls, meta: "VehicleMaterialMetadata", data: Dict[str, Any]) -> None:
        """Parse one JBeam part object and update ``meta`` in place."""

        def _as_string_list(value: Any) -> List[str]:
            """Normalize a string-or-list value to a list of non-empty strings."""
            if value is None:
                return []
            if isinstance(value, str) and value:
                return [value]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value if v not in (None, "")]
            return []

        glow_raw = data.get("glowMap", {})
        if isinstance(glow_raw, dict):
            for org_mat, value in glow_raw.items():
                if not isinstance(value, dict):
                    continue
                entry = GlowMapEntry(
                    org_mat=org_mat,
                    off=value.get("off"),
                    on=value.get("on"),
                    on_intense=value.get("on_intense"),
                    simple_function=value.get("simpleFunction"),
                    advanced_function=value.get("advancedFunction"),
                    material_emissive_scaling=value.get("materialEmissiveScaling"),
                    limit=value.get("limit"),
                )
                meta.glow_maps[org_mat] = entry
                meta.electrics.update(entry.electrics_names)

        # Build deformGroup -> damaged material from flexbodies.
        flexbodies = data.get("flexbodies", [])
        if isinstance(flexbodies, dict):
            flexbodies = flexbodies.values()
        for fb in flexbodies:
            if not isinstance(fb, dict):
                continue
            groups = _as_string_list(fb.get("deformGroup"))
            dmg_raw = fb.get("deformMaterialDamaged")
            dmg_list = _as_string_list(dmg_raw)
            if not groups:
                continue
            for i, group in enumerate(groups):
                entry = meta._deform_group(group)
                if i < len(dmg_list):
                    entry.damaged_material = dmg_list[i]
                elif dmg_list:
                    entry.damaged_material = dmg_list[0]

        # Walk beams to count beams per group and collect trigger ratios.
        beams = data.get("beams", [])
        current_groups: List[str] = []
        current_ratio: Optional[float] = None
        for item in beams:
            if isinstance(item, dict):
                new_groups = _as_string_list(item.get("deformGroup"))
                ratio = item.get("deformationTriggerRatio")

                if new_groups:
                    current_groups = new_groups
                    if isinstance(ratio, (int, float)):
                        current_ratio = float(ratio)
                    for group in current_groups:
                        if current_ratio is not None:
                            meta._deform_group(group).trigger_ratios.append(current_ratio)
                elif new_groups or item.get("deformGroup") == "" or item.get("deformGroup") == []:
                    current_groups = []
                    current_ratio = None

                if isinstance(ratio, (int, float)) and not new_groups:
                    current_ratio = float(ratio)
            elif isinstance(item, (list, tuple)) and current_groups:
                for group in current_groups:
                    meta._deform_group(group).beam_count += 1

    def _deform_group(self, name: str) -> DeformGroupEntry:
        if name not in self.deform_groups:
            self.deform_groups[name] = DeformGroupEntry(name=name)
        return self.deform_groups[name]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "glow_maps": {k: self._glow_to_dict(v) for k, v in self.glow_maps.items()},
            "deform_groups": {k: self._deform_to_dict(v) for k, v in self.deform_groups.items()},
            "electrics": sorted(self.electrics),
        }

    def _glow_to_dict(self, entry: GlowMapEntry) -> Dict[str, Any]:
        d: Dict[str, Any] = {"org_mat": entry.org_mat}
        for attr in ("off", "on", "on_intense", "simple_function", "advanced_function", "material_emissive_scaling", "limit"):
            v = getattr(entry, attr)
            if v is not None:
                d[attr] = v
        return d

    def _deform_to_dict(self, entry: DeformGroupEntry) -> Dict[str, Any]:
        return {
            "name": entry.name,
            "damaged_material": entry.damaged_material,
            "trigger_ratios": entry.trigger_ratios,
            "beam_count": entry.beam_count,
        }

    def link_to_library(self, library: Any) -> None:
        """Attach runtime metadata to materials that exist in a library."""
        for org_mat, glow in self.glow_maps.items():
            if org_mat in library:
                mat = library[org_mat]
                if not hasattr(mat, "metadata") or mat.metadata is None:
                    mat.metadata = MaterialRuntimeMetadata()
                mat.metadata.glow_map = glow
                mat.metadata.electrics.update(glow.electrics_names)

        for entry in self.deform_groups.values():
            if entry.damaged_material and entry.damaged_material in library:
                mat = library[entry.damaged_material]
                if not hasattr(mat, "metadata") or mat.metadata is None:
                    mat.metadata = MaterialRuntimeMetadata()
                mat.metadata.deform_groups.append(entry)


def load_jbeam_metadata(path: str | Path) -> VehicleMaterialMetadata:
    """Load runtime material metadata from a JBeam vehicle file."""
    text = Path(path).read_text(encoding="utf-8")
    data = sjson.parse(text)
    return VehicleMaterialMetadata.from_jbeam(data)
