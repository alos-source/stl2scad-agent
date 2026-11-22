"""Stage 1: STL mesh inspection, health checks, shell splitting, symmetry & organic score."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh

from stl2scad.config import DEFAULT_GATES
from stl2scad.schemas import AnalysisReport, BBoxInfo, ShellSummary, SymmetryInfo


def _round_list(vals: np.ndarray | list[float], digits: int = 4) -> list[float]:
    return [round(float(v), digits) for v in vals]


def _bbox_info(mesh: trimesh.Trimesh) -> BBoxInfo:
    bmin, bmax = mesh.bounds
    extents = bmax - bmin
    center = (bmin + bmax) / 2.0
    return BBoxInfo(
        min=_round_list(bmin),
        max=_round_list(bmax),
        extents=_round_list(extents),
        center=_round_list(center),
    )


def _count_non_manifold_edges(mesh: trimesh.Trimesh) -> int:
    # An edge in a closed 2-manifold is shared by exactly 2 faces.
    edges = np.sort(mesh.edges, axis=1)
    if len(edges) == 0:
        return 0
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(np.sum(counts != 2))


def _bed_contact_area(mesh: trimesh.Trimesh, tol: float = 0.05) -> tuple[float, float]:
    z_min = float(mesh.bounds[0][2])
    face_z = mesh.triangles[:, :, 2]
    on_bed = np.all(np.abs(face_z - z_min) <= tol, axis=1)
    area = float(np.sum(mesh.area_faces[on_bed]))
    return round(area, 4), round(z_min, 4)


def _detect_symmetry(mesh: trimesh.Trimesh, sample_count: int = 800) -> SymmetryInfo:
    if len(mesh.faces) == 0:
        return SymmetryInfo()
    pts, _ = trimesh.sample.sample_surface(mesh, sample_count, seed=42)
    center = (mesh.bounds[0] + mesh.bounds[1]) / 2.0
    diag = float(np.linalg.norm(mesh.extents))
    tol = max(0.25, diag * 0.004)
    pq = trimesh.proximity.ProximityQuery(mesh)

    # Mirror X
    mir_x = pts.copy()
    mir_x[:, 0] = 2.0 * center[0] - mir_x[:, 0]
    _, d_x, _ = pq.on_surface(mir_x)
    mirror_x = bool(np.percentile(d_x, 95) <= tol)

    # Mirror Y
    mir_y = pts.copy()
    mir_y[:, 1] = 2.0 * center[1] - mir_y[:, 1]
    _, d_y, _ = pq.on_surface(mir_y)
    mirror_y = bool(np.percentile(d_y, 95) <= tol)

    # Rotational N-fold symmetry around Z axis through XY center
    best_order: int | None = None
    best_err: float | None = None
    rel = pts - np.array([center[0], center[1], 0.0])
    for n in (8, 6, 5, 4, 3, 2):
        angle = 2.0 * math.pi / n
        c, s = math.cos(angle), math.sin(angle)
        rot = rel.copy()
        rot[:, 0] = c * rel[:, 0] - s * rel[:, 1] + center[0]
        rot[:, 1] = s * rel[:, 0] + c * rel[:, 1] + center[1]
        _, d_r, _ = pq.on_surface(rot)
        err95 = float(np.percentile(d_r, 95))
        if err95 <= tol:
            best_order = n
            best_err = round(err95, 4)
            break

    return SymmetryInfo(
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        rotational_order=best_order,
        rotational_error_mm=best_err,
    )


def _compute_organic_score(mesh: trimesh.Trimesh) -> float:
    """Estimate fraction of surface area that is non-CAD / freeform organic."""
    total_area = float(mesh.area)
    if total_area <= 1e-9:
        return 0.0

    normals = mesh.face_normals
    areas = mesh.area_faces
    nz = np.abs(normals[:, 2])

    cad_mask = (nz >= 0.985) | (nz <= 0.05)

    angles_deg = np.degrees(np.arccos(np.clip(nz, 0.0, 1.0)))
    bins = np.floor(angles_deg / 2.0).astype(int)
    for b in np.unique(bins):
        mask = bins == b
        if float(np.sum(areas[mask])) / total_area >= 0.015:
            cad_mask |= mask

    try:
        for facet in mesh.facets:
            if len(facet) >= 3 or float(np.sum(areas[facet])) / total_area >= 0.005:
                cad_mask[facet] = True
    except Exception:
        pass

    cad_area = float(np.sum(areas[cad_mask]))
    organic = max(0.0, min(1.0, 1.0 - cad_area / total_area))
    return round(organic, 4)


def load_mesh(stl_path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(str(stl_path), force="mesh")
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError(f"No mesh geometry found in {stl_path}")
        return trimesh.util.concatenate(meshes)
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"Unsupported geometry type in {stl_path}: {type(loaded)}")
    return loaded


def inspect_stl(
    stl_path: Path,
    out_dir: Path,
    organic_threshold: float = DEFAULT_GATES.organic_warn_threshold,
) -> AnalysisReport:
    """Run Stage 1 inspection and write analysis.json (+ split shells if multi-part)."""
    stl_path = Path(stl_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    mesh = load_mesh(stl_path)
    raw_shells = mesh.split(only_watertight=False)
    if not raw_shells:
        raw_shells = [mesh]
    raw_shells.sort(key=lambda m: abs(float(m.volume)) if m.is_watertight else float(m.area), reverse=True)

    shells_info: list[ShellSummary] = []
    shells_dir = out_dir / "shells"
    if len(raw_shells) > 1:
        shells_dir.mkdir(parents=True, exist_ok=True)

    for idx, sh in enumerate(raw_shells):
        shell_file: str | None = None
        if len(raw_shells) > 1:
            p = shells_dir / f"part_{idx}.stl"
            sh.export(str(p))
            shell_file = str(p)
        shells_info.append(
            ShellSummary(
                index=idx,
                file=shell_file,
                faces=int(len(sh.faces)),
                vertices=int(len(sh.vertices)),
                watertight=bool(sh.is_watertight),
                volume_mm3=round(float(abs(sh.volume)), 4),
                surface_area_mm2=round(float(sh.area), 4),
                bbox=_bbox_info(sh),
            )
        )

    bed_area, bed_z = _bed_contact_area(mesh)
    non_manifold = _count_non_manifold_edges(mesh)
    symmetry = _detect_symmetry(mesh)
    organic_score = _compute_organic_score(mesh)
    organic_warning = organic_score > organic_threshold

    notes: list[str] = []
    if not mesh.is_watertight:
        notes.append("Input mesh is not watertight; volume/boolean metrics may use fallback.")
    if len(raw_shells) > 1:
        notes.append(f"Detected {len(raw_shells)} disconnected shells; exported to shells/part_*.stl.")
    if organic_warning:
        notes.append(
            f"High organic/freeform score ({organic_score:.2f} > {organic_threshold:.2f}). "
            "Mesh may not be suitable for pure parametric CSG reconstruction."
        )

    report = AnalysisReport(
        input_file=str(stl_path),
        faces=int(len(mesh.faces)),
        vertices=int(len(mesh.vertices)),
        watertight=bool(mesh.is_watertight),
        is_winding_consistent=bool(mesh.is_winding_consistent),
        euler_number=int(mesh.euler_number),
        non_manifold_edges=non_manifold,
        volume_mm3=round(float(abs(mesh.volume)), 4),
        surface_area_mm2=round(float(mesh.area), 4),
        bbox=_bbox_info(mesh),
        bed_contact_area_mm2=bed_area,
        bed_contact_z=bed_z,
        shell_count=len(raw_shells),
        shells=shells_info,
        symmetry=symmetry,
        organic_score=organic_score,
        organic_warning=organic_warning,
        notes=notes,
    )

    (out_dir / "analysis.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
