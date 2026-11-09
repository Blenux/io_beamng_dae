"""Enumerated values used by BeamNG material definitions."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class IntEnum(int, Enum):
    def __str__(self) -> str:
        return str(self.value)


class MaterialVersion(StrEnum):
    """Material format version as written to ``main.materials.json``."""
    V1 = "1.0"
    V1_5 = "1.5"


class AlphaBlendMode(StrEnum):
    """``translucentBlendOp`` values."""
    NONE = "None"
    PRE_MUL_ALPHA = "PreMulAlpha"
    ADD = "Add"
    ADD_ALPHA = "AddAlpha"
    LERP_ALPHA = "LerpAlpha"
    MUL = "Mul"
    SUB = "Sub"


class NormalMapSpace(StrEnum):
    DIRECTX = "DirectX"
    OPENGL = "OpenGL"


class TextureInterpolation(StrEnum):
    LINEAR = "Linear"
    CLOSEST = "Closest"
    CUBIC = "Cubic"
    SMART = "Smart"


class TextureProjection(StrEnum):
    FLAT = "Flat"
    BOX = "Box"
    SPHERE = "Sphere"
    TUBE = "Tube"


class TextureExtension(StrEnum):
    REPEAT = "Repeat"
    EXTEND = "Extend"
    CLIP = "Clip"
    MIRROR = "Mirror"


class ImageSource(StrEnum):
    FILE = "File"
    SEQUENCE = "Sequence"
    MOVIE = "Movie"
    GENERATED = "Generated"
    TILED = "Tiled"
