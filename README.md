# `stl2scad` — Hybrid STL-to-OpenSCAD Converter

`stl2scad` is a **script-first, agent-coordinated** reverse-engineering pipeline that converts functional, mechanical `.stl` meshes into clean, modular, parametric OpenSCAD (`.scad`) source files.

Instead of dumping raw `polyhedron()` triangles, `stl2scad` separates exact geometry work from high-level CAD synthesis:
- **Deterministic Python scripts (`uv`-managed)** handle mesh health checks, disconnected-shell splitting, symmetry detection, adaptive Z-slicing, 2D/3D feature & pattern extraction, geometric diffing (IoU, Chamfer, Hausdorff, deviation hotspots), and visual diff composites.
- **External CAD & slicer CLIs** (**OpenSCAD**, plus optional **CuraEngine**, **PrusaSlicer**, or **OrcaSlicer**) compile `.scad` → `.stl`, render synchronized multi-angle previews, and validate FDM printability.
- **An Antigravity agent skill** ([skills/stl2scad/SKILL.md](skills/stl2scad/SKILL.md)) orchestrates the pipeline, interprets design intent, writes readable parametric OpenSCAD code, and iterates on error hotspots & visual diffs until quality gates pass.

---

## Requirements

- **Python 3.12+** managed with **[`uv`](https://docs.astral.sh/uv/)**
- **OpenSCAD** (2021.01 or nightly with Manifold backend — auto-detected in `PATH` and `C:\Program Files\OpenSCAD\`, or set `OPENSCAD_BIN`)
- **Optional Slicer CLI** for Stage 6 slicing comparison (auto-detected, or set via env var):
  - UltiMaker Cura (`CuraEngine` / `CURAENGINE_BIN`)
  - PrusaSlicer (`prusa-slicer-console` / `PRUSASLICER_BIN`)
  - OrcaSlicer (`orca-slicer` / `ORCASLICER_BIN`)
  - Generic override: `SLICER_BIN`

> If no external slicer CLI is installed, `stl2scad validate` gracefully skips external slicing and still runs built-in manifold, bed-contact, and >45° overhang checks.

---

## Setup

```powershell
uv sync
uv run stl2scad doctor
```

---

## CLI Usage

### 1. Analyze an Input STL (Stage 1 + Stage 2)
Inspects mesh health, splits multi-part shells, detects symmetry/organic score, extracts Z-slices, contours, planes, grids, and polar lobe patterns, and writes `analysis.json`, `features.json`, and `slices/*.png`:

```powershell
uv run stl2scad analyze path/to/input.stl --out build/my_part
```

Add `--render-previews` to also render orthographic/isometric reference PNGs of the input STL via OpenSCAD.

### 2. Verify a Reconstructed `.scad` File (Stage 4 + Stage 5 + Stage 6)
Compiles `model.scad` with OpenSCAD, generates side-by-side & pixel-diff PNGs (`diff_iso.png`, `diff_top.png`, `diff_side.png`), computes geometric accuracy metrics + deviation hotspots (`eval_report.json`), and validates printability (`validation_report.json`):

```powershell
uv run stl2scad verify build/my_part/model.scad path/to/input.stl --out build/my_part
```

### 3. Individual Pipeline Stages

| Command | Description |
| :--- | :--- |
| `uv run stl2scad doctor` | Check detected OpenSCAD and slicer CLI paths |
| `uv run stl2scad inspect <stl> -o <dir>` | Stage 1: mesh health, shell splitting, symmetry, organic score |
| `uv run stl2scad extract <stl> -o <dir>` | Stage 2: Z-slices, 2D contours, planes, grids, polar patterns |
| `uv run stl2scad render <scad> <stl> -o <dir>` | Stage 4: OpenSCAD compile + synchronized visual diff composites |
| `uv run stl2scad evaluate <in.stl> <rec.stl> -o <dir>` | Stage 5: IoU, Chamfer, Hausdorff, and `"missing"`/`"excess"` hotspots |
| `uv run stl2scad validate <in.stl> <rec.stl> -o <dir>` | Stage 6: manifold, bed contact, overhangs, optional Cura/Prusa/Orca slice |
| `uv run stl2scad verify <scad> <stl> -o <dir> [--track --note "..."]` | Run render + evaluate + validate (optionally logging iteration history) |
| `uv run stl2scad report <scad> <stl> -o <dir>` | Generate optional `generation_report.md` & `.json` (iterations, assumptions, challenges) |
| `uv run stl2scad benchmark --refs References -o build/benchmark` | Run analysis across all reference STLs in `References/` |

`References/` is optional, local-only benchmark data and is intentionally excluded from Git.

---

## Using with an Antigravity Agent

The closed-loop conversion workflow is documented in [skills/stl2scad/SKILL.md](skills/stl2scad/SKILL.md):
1. Agent runs `uv run stl2scad analyze <input.stl> --out <job_dir>`.
2. If `organic_warning` is `true`, the agent skips conversion and reports why.
3. Otherwise, the agent reads `analysis.json`, `features.json`, and `slices/*.png` and authors a clean, parametric `model.scad`.
4. Agent runs `uv run stl2scad verify <job_dir>/model.scad <input.stl> --out <job_dir>`.
5. Agent inspects `eval_report.json` (`hotspots`) and `diff_*.png`, refining `model.scad` for up to 5 iterations until `passed_gates == true`.
6. Agent writes `conversion_log.json` and a part-specific `README.md`.

---

## Default Quality Gates

| Metric | Pass Threshold |
| :--- | :--- |
| **Manifold / Watertight** | `true` (`0` non-manifold edges) |
| **Bounding Box Error** | ≤ 0.20 mm per axis |
| **Volume Error** | ≤ 2.0% |
| **Volumetric IoU** | ≥ 0.96 (`manifold3d` boolean / voxel fallback) |
| **Mean Surface Error (Chamfer)** | ≤ 0.20 mm |
| **95th-Percentile Hausdorff** | ≤ 0.35 mm |

---

## Project Structure

```text
stl2scad/
├── pyproject.toml              # uv project & dependencies
├── Task.md                     # User stories & technical specification
├── README.md                   # This file
├── LICENSE                     # MIT license
├── src/stl2scad/
│   ├── cli.py                  # Typer CLI entry point
│   ├── config.py               # Quality gates & OpenSCAD/slicer discovery
│   ├── schemas.py              # Pydantic JSON schemas
│   ├── inspect_stl.py          # Stage 1: mesh health, shells, symmetry, organic score
│   ├── extract_features.py     # Stage 2: Z-slicing, contours, planes, grids, polar lobes
│   ├── render_compare.py       # Stage 4: OpenSCAD CLI & visual diff composites
│   ├── evaluate_diff.py        # Stage 5: IoU, Chamfer, Hausdorff, deviation hotspots
│   └── validate_slicer.py      # Stage 6: printability & Cura/Prusa/Orca validation
├── skills/stl2scad/
│   └── SKILL.md                # Antigravity agent coordination skill
├── tests/
│   └── test_pipeline.py        # Unit & reference-model integration tests
└── .github/workflows/ci.yml    # Automated test workflow
```

---

## Running Tests

```powershell
uv run pytest -v
```
