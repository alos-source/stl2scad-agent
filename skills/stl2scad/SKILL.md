---
name: stl2scad
description: Reverse-engineer a functional/mechanical .stl mesh into a clean, modular, parametric OpenSCAD (.scad) model using deterministic Python process scripts, OpenSCAD CLI rendering, optional Cura/Prusa/Orca slicer validation, and iterative visual/metric refinement.
---

# `stl2scad` — STL-to-OpenSCAD Reverse-Engineering Skill

Follow this closed-loop workflow whenever the user asks to convert an `.stl` file into parametric OpenSCAD code.

## Core Principles
1. **Script-First Measurement:** Never guess dimensions. Always run `uv run stl2scad analyze` first and base every dimension on `analysis.json`, `features.json`, and the diagnostic slice plots.
2. **Clean Parametric CSG Only:** Write modular, human-readable OpenSCAD (`union`, `difference`, `hull`, `linear_extrude`, `rotate_extrude`, primitives) with named parameters. Never dump raw `polyhedron()` triangles.
3. **Skip Organic Meshes:** If `analysis.json` sets `"organic_warning": true`, stop and inform the user that the mesh is too freeform/organic for clean parametric CSG reconstruction.
4. **Iterate with Evidence:** Use `eval_report.json` (especially `hotspots` showing `"missing"` vs. `"excess"` material coordinates) and `diff_*.png` composites to drive targeted edits until `passed_gates == true` (max 5 iterations).

---

## Step-by-Step Workflow

### Step 1: Analyze the Input STL
Run the deterministic inspection and feature extraction scripts:
```powershell
uv run stl2scad analyze <input.stl> --out <job_dir>
```
*(Optionally add `--render-previews` to generate orthographic/isometric reference renders of the input STL via OpenSCAD; note that on Windows OpenSCAD CLI may require running outside the sandbox.)*

Inspect:
- `<job_dir>/analysis.json`: bounding box, extents, volume, watertightness, `shell_count` (and `shells/part_*.stl` if multi-part), `symmetry`, and `organic_score`.
- `<job_dir>/features.json`: `z_transitions_mm`, `slices` (circles, hexagons with wrench size `s`, rounded rects, rects), `planes` (horizontal, vertical, 45° chamfers), `grid_patterns`, and `polar_patterns`.
- `<job_dir>/slices/z_*.png`: visual 2D cross-sections at each Z-band.

If `shell_count > 1`, run `uv run stl2scad extract <job_dir>/shells/part_<i>.stl --out <job_dir>/part_<i>` for each shell so each component can be reconstructed as its own OpenSCAD `module`.

### Step 2: Synthesize Parametric `model.scad`
Write `<job_dir>/model.scad` following these conventions:
- Header comment with source STL name, date, and verification checklist.
- Parameter groups using OpenSCAD Customizer syntax:
  - `/* [Main Dimensions] */`
  - `/* [Features] */`
  - `/* [Quality] */` (`$fn = $preview ? 32 : 64;`)
- Round obvious CAD dimensions to clean engineering values (e.g., `119.998 -> 120.0`, `2.001 -> 2.0`, `10.35` for M6 hex pocket) while keeping exact match to the input coordinate origin (`bbox.min` / `bbox.center`).
- If `shell_count > 1`, include a `part` selector (`"assembly"`, `"print_bed"`, plus individual part names) and default to `"assembly"` matching the input STL layout.

### Step 3: Verify (Compile, Render, Evaluate, Validate)
Run the unified verification command (add `--track --note "<what changed>"` to record iteration history for the optional generation report):
```powershell
uv run stl2scad verify <job_dir>/model.scad <input.stl> --out <job_dir> --track --note "Initial parametric synthesis"
```
*(On Windows, run this command outside the sandbox if `openscad.com` hits a sandbox DLL restriction.)*

Inspect:
- `<job_dir>/render_log.json`: confirm `compile_exit_code == 0` and 0 errors/warnings.
- `<job_dir>/eval_report.json`: check `passed_gates`, `volumetric_iou`, `volume_error_pct`, `max_bbox_error_mm`, `chamfer_mean_mm`, `hausdorff_95_mm`, and `hotspots`.
- `<job_dir>/diff_iso.png`, `diff_top.png`, `diff_side.png`: visually verify alignment and silhouettes.
- `<job_dir>/validation_report.json`: confirm `watertight == true`, `non_manifold_edges == 0`, and check slicer output (`cura`, `prusa`, `orca`, or `skipped_not_installed`).

### Step 4: Iterative Refinement (Up to 5 Iterations)
If `passed_gates == false`:
1. Check `centroid_offset_mm` first — fix any global translation/origin mismatch before tweaking individual features.
2. Address the top `hotspots`:
   - `kind == "missing"`: input STL has geometry missing from `model.scad` at `centroid_mm`.
   - `kind == "excess"`: `model.scad` has extra geometry (or an under-sized cutout) at `centroid_mm`.
3. Edit `model.scad` and re-run `uv run stl2scad verify ... --track --note "<fixes applied>"`.

### Step 5: Optional Generation Report
When the user wants a generation report (or for any multi-iteration conversion), run:
```powershell
uv run stl2scad report <job_dir>/model.scad <input.stl> --out <job_dir> `
  --summary "<concise design interpretation>" `
  --assumption "<key modeling assumption>" `
  --challenge "<challenge or trade-off encountered>"
```
This writes `<job_dir>/generation_report.md` and `<job_dir>/generation_report.json` combining the full iteration history, geometric metrics, printability checks, and your documented assumptions & challenges.
