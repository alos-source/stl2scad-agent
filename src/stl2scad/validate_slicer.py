"""Stage 6: Printability validation (manifold, bed contact, overhangs) & optional Cura/Prusa/Orca slicer check."""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np
import trimesh

from stl2scad.config import SlicerKind, find_slicer
from stl2scad.inspect_stl import _bed_contact_area, _count_non_manifold_edges, load_mesh
from stl2scad.schemas import SlicerMetrics, ValidationReport


def _overhang_metrics(mesh: trimesh.Trimesh, max_angle_deg: float = 45.0, bed_tol: float = 0.05) -> tuple[float, float]:
    """Compute area of downward-facing triangles steeper than max_angle_deg from vertical, excluding the bed."""
    total_area = float(mesh.area)
    if total_area <= 1e-9:
        return 0.0, 0.0
    z_min = float(mesh.bounds[0][2])
    face_z = mesh.triangles[:, :, 2]
    on_bed = np.all(np.abs(face_z - z_min) <= bed_tol, axis=1)
    # Normal z < -cos(90 - max_angle) = -sin(max_angle) indicates overhang steeper than max_angle_deg
    nz_limit = -math.sin(math.radians(max_angle_deg)) - 0.02
    overhang_mask = (mesh.face_normals[:, 2] < nz_limit) & (~on_bed)
    overhang_area = float(np.sum(mesh.area_faces[overhang_mask]))
    return round(overhang_area, 4), round(overhang_area / total_area * 100.0, 3)


def _parse_gcode_summary(gcode_path: Path) -> tuple[int | None, float | None]:
    if not gcode_path.is_file():
        return None, None
    text = gcode_path.read_text(encoding="utf-8", errors="ignore")
    layers: int | None = None
    filament_mm3: float | None = None

    m_layer = re.search(r";\s*LAYER_COUNT:\s*(\d+)", text)
    if m_layer:
        layers = int(m_layer.group(1))
    else:
        layer_tags = re.findall(r";\s*LAYER_CHANGE", text)
        if layer_tags:
            layers = len(layer_tags)

    m_vol = re.search(r"filament used \[cm3\]\s*=\s*([\d.]+)", text, re.IGNORECASE)
    if m_vol:
        filament_mm3 = round(float(m_vol.group(1)) * 1000.0, 2)
    else:
        m_cura = re.search(r";\s*Filament used:\s*([\d.]+)m", text)
        if m_cura:
            # 1.75 mm filament cross-section area ~ 2.405 mm^2
            filament_mm3 = round(float(m_cura.group(1)) * 1000.0 * (math.pi * (1.75 / 2.0) ** 2), 2)
    return layers, filament_mm3


def _slice_one(kind: SlicerKind, slicer_bin: Path, stl_path: Path, out_gcode: Path) -> bool:
    if kind == "prusa":
        cmd = [str(slicer_bin), "--export-gcode", "--layer-height", "0.2", "-o", str(out_gcode), str(stl_path)]
    elif kind == "orca":
        cmd = [str(slicer_bin), "--slice", "0", "--outputdir", str(out_gcode.parent), str(stl_path)]
    else:  # cura
        cmd = [str(slicer_bin), "slice", "-l", str(stl_path), "-o", str(out_gcode)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    return proc.returncode == 0 and out_gcode.is_file()


def validate_printability(
    input_stl: Path,
    reconstructed_stl: Path,
    out_dir: Path,
    preferred_slicer: SlicerKind | None = None,
) -> ValidationReport:
    """Validate manifoldness, bed contact, overhangs, and run Cura/Prusa/Orca slicer if installed."""
    input_stl = Path(input_stl).resolve()
    reconstructed_stl = Path(reconstructed_stl).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    mesh = load_mesh(reconstructed_stl)
    watertight = bool(mesh.is_watertight)
    non_manifold = _count_non_manifold_edges(mesh)
    bed_area, _ = _bed_contact_area(mesh)
    overhang_area, overhang_pct = _overhang_metrics(mesh)

    notes: list[str] = []
    if not watertight:
        notes.append("Reconstructed mesh is not watertight.")
    if non_manifold > 0:
        notes.append(f"Reconstructed mesh has {non_manifold} non-manifold edges.")

    slicer_found = find_slicer(preferred=preferred_slicer)
    slicer_metrics: SlicerMetrics | None = None
    if slicer_found is None:
        slicer_status = "skipped_not_installed"
        notes.append(
            "No external slicer CLI (CuraEngine, PrusaSlicer, OrcaSlicer) found; "
            "completed built-in manifold, bed-contact, and overhang checks."
        )
    else:
        kind, slicer_bin = slicer_found
        g_in = out_dir / "_slice_input.gcode"
        g_rec = out_dir / "_slice_reconstructed.gcode"
        ok_in = _slice_one(kind, slicer_bin, input_stl, g_in)
        ok_rec = _slice_one(kind, slicer_bin, reconstructed_stl, g_rec)
        if ok_rec:
            in_layers, in_vol = _parse_gcode_summary(g_in) if ok_in else (None, None)
            rec_layers, rec_vol = _parse_gcode_summary(g_rec)
            slicer_status = "ok"
            slicer_metrics = SlicerMetrics(
                slicer=kind,
                slicer_bin=str(slicer_bin),
                status="ok",
                input_layers=in_layers,
                reconstructed_layers=rec_layers,
                input_filament_mm3=in_vol,
                reconstructed_filament_mm3=rec_vol,
            )
        else:
            slicer_status = "failed"
            slicer_metrics = SlicerMetrics(
                slicer=kind,
                slicer_bin=str(slicer_bin),
                status="failed",
                notes=[f"{kind} CLI returned non-zero exit code (may require machine definition profile)."],
            )
            notes.append(f"External slicer ({kind}) failed; built-in checks still recorded.")

    passed = watertight and non_manifold == 0
    report = ValidationReport(
        input_stl=str(input_stl),
        reconstructed_stl=str(reconstructed_stl),
        watertight=watertight,
        non_manifold_edges=non_manifold,
        bed_contact_area_mm2=bed_area,
        overhang_area_gt45_mm2=overhang_area,
        overhang_ratio_pct=overhang_pct,
        slicer_status=slicer_status,  # type: ignore[arg-type]
        slicer_metrics=slicer_metrics,
        passed=passed,
        notes=notes,
    )
    (out_dir / "validation_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
