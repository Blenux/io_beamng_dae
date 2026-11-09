# Changelog

## 2.1.0 — Full BeamNG materials pipeline

- Complete stage/material key coverage driven by vendored key maps (~120 stage keys, ~40 material keys); unknown keys preserved in `extra_json` for lossless round-trip
- GlowMap runtime: JBeam `glowMap` auto-population, off/on/intense state switching, BeamNG sidebar with electrics editor
- `MaterialLibrary` resolution chain for DAE and standalone `.materials.json` import: glowMap → skin variant → `mapTo` → direct key → normalized name
- VFS mounts + vehicle paint resolution, texture extension/case fallbacks (`.png`↔`.dds`↔`.DDS`)
- Index-based `*MapUseUV` → named UV layers (0–3) with per-material `bng_uv_names` recording for tvert-named channels
- Eevee/Cycles transparency semantics (DITHERED/BLENDED by `translucentZWrite`, `use_transparency_overlap`, shadow culling)
- Materials validation operator + `scripts/audit_materials.py` / `verify_import_headless.py` automation + `tests/` corpus coverage
- Privacy: local-only game-path detection, guarded against FS errors
- `[permissions] files` declared for reading BeamNG game content

## 2.0.0 — Pure-Python backend

- Replaced the pybind11 `cdae_native` C++ module with pure-Python `io_beamng_dae/native/` (DAE XML I/O, msgpack CDAE I/O, zstandard)
- Feature parity with the C++ variant (`io_beamng_dae_dev` 1.x); required for official Blender Extensions platform (no pre-compiled wheels)

## 1.0.x — C++ native backend (io_beamng_dae_dev)

- Native `cdae_native` extension for fastest parse/write of DAE/CDAE and materials JSON
- Superseded by 2.x; users on 1.0.x upgrade to the Python build via the same extension id
