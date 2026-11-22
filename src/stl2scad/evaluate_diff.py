"""Stage 5: Quantitative geometric comparison (IoU, Chamfer, Hausdorff, deviation hotspots)."""

from __future__ import annotations

from pathlib import Path

import manifold3d
import numpy as np
import trimesh
from scipy.cluster.hierarchy import fclusterdata

from stl2scad.config import DEFAULT_GATES, QualityGates
from stl2scad.inspect_stl import load_mesh
from stl2scad.schemas import DeviationHotspot, EvalReport


def _to_manifold(mesh: trimesh.Trimesh) -> manifold3d.Manifold:
    m_mesh = manifold3d.Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    )
    return manifold3d.Manifold(m_mesh)


def _compute_volumetric_iou(mesh_a: trimesh.Trimesh, mesh_b: trimesh.Trimesh) -> tuple[float, str]:
    """Compute exact volumetric IoU via manifold3d boolean, falling back to voxel grid."""
    if mesh_a.is_watertight and mesh_b.is_watertight:
        try:
            ma = _to_manifold(mesh_a)
            mb = _to_manifold(mesh_b)
            if ma.status() == manifold3d.Error.NoError and mb.status() == manifold3d.Error.NoError:
                inter_vol = float((ma ^ mb).volume())
                union_vol = float((ma + mb).volume())
                if union_vol > 1e-9:
                    return max(0.0, min(1.0, inter_vol / union_vol)), "manifold_boolean"
        except Exception:
            pass

    bmin = np.minimum(mesh_a.bounds[0], mesh_b.bounds[0])
    bmax = np.maximum(mesh_a.bounds[1], mesh_b.bounds[1])
    pitch = max(0.5, float(np.max(bmax - bmin)) / 48.0)
    va = mesh_a.voxelized(pitch=pitch).fill()
    vb = mesh_b.voxelized(pitch=pitch).fill()
    pts_a = {tuple(np.round(p / pitch).astype(int)) for p in va.points}
    pts_b = {tuple(np.round(p / pitch).astype(int)) for p in vb.points}
    union = len(pts_a | pts_b)
    iou = (len(pts_a & pts_b) / union) if union > 0 else 0.0
    return max(0.0, min(1.0, iou)), "voxel"


def _extract_hotspots(
    pts: np.ndarray,
    dists: np.ndarray,
    kind: str,
    threshold_mm: float,
    cluster_radius_mm: float = 8.0,
) -> list[DeviationHotspot]:
    mask = dists > threshold_mm
    if not np.any(mask):
        return []
    bad_pts = pts[mask]
    bad_d = dists[mask]

    if len(bad_pts) == 1:
        labels = np.array([1])
    else:
        if len(bad_pts) > 1500:
            order = np.argsort(bad_d)[-1500:]
            bad_pts = bad_pts[order]
            bad_d = bad_d[order]
        labels = fclusterdata(bad_pts, t=cluster_radius_mm, criterion="distance")

    spots: list[DeviationHotspot] = []
    for cid in np.unique(labels):
        cmask = labels == cid
        cpts = bad_pts[cmask]
        cd = bad_d[cmask]
        centroid = np.mean(cpts, axis=0)
        spots.append(
            DeviationHotspot(
                rank=0,
                kind=kind,  # type: ignore[arg-type]
                centroid_mm=[round(float(v), 2) for v in centroid],
                max_error_mm=round(float(np.max(cd)), 4),
                mean_error_mm=round(float(np.mean(cd)), 4),
                point_count=int(len(cpts)),
            )
        )
    return spots


def evaluate_diff(
    input_stl: Path,
    reconstructed_stl: Path,
    out_dir: Path,
    gates: QualityGates = DEFAULT_GATES,
    sample_count: int = 5000,
) -> EvalReport:
    """Compare original and reconstructed STLs and write eval_report.json."""
    input_stl = Path(input_stl).resolve()
    reconstructed_stl = Path(reconstructed_stl).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    mesh_in = load_mesh(input_stl)
    mesh_rec = load_mesh(reconstructed_stl)

    bbox_delta = np.abs(mesh_in.extents - mesh_rec.extents)
    max_bbox_err = float(np.max(bbox_delta))
    centroid_offset = mesh_rec.bounds.mean(axis=0) - mesh_in.bounds.mean(axis=0)

    vol_in = float(abs(mesh_in.volume))
    vol_rec = float(abs(mesh_rec.volume))
    vol_err_pct = (abs(vol_rec - vol_in) / vol_in * 100.0) if vol_in > 1e-9 else 0.0

    area_in = float(mesh_in.area)
    area_rec = float(mesh_rec.area)
    area_err_pct = (abs(area_rec - area_in) / area_in * 100.0) if area_in > 1e-9 else 0.0

    iou, iou_method = _compute_volumetric_iou(mesh_in, mesh_rec)

    pts_in, _ = trimesh.sample.sample_surface(mesh_in, sample_count, seed=42)
    pts_rec, _ = trimesh.sample.sample_surface(mesh_rec, sample_count, seed=43)

    pq_in = trimesh.proximity.ProximityQuery(mesh_in)
    pq_rec = trimesh.proximity.ProximityQuery(mesh_rec)

    _, d_in_to_rec, _ = pq_rec.on_surface(pts_in)  # missing material in recon
    _, d_rec_to_in, _ = pq_in.on_surface(pts_rec)  # excess material in recon

    chamfer_mean = float(0.5 * (np.mean(d_in_to_rec) + np.mean(d_rec_to_in)))
    hausdorff_95 = float(max(np.percentile(d_in_to_rec, 95), np.percentile(d_rec_to_in, 95)))
    hausdorff_max = float(max(np.max(d_in_to_rec), np.max(d_rec_to_in)))

    hotspots = _extract_hotspots(pts_in, d_in_to_rec, "missing", gates.max_hausdorff_95_mm)
    hotspots += _extract_hotspots(pts_rec, d_rec_to_in, "excess", gates.max_hausdorff_95_mm)
    hotspots.sort(key=lambda h: (h.max_error_mm, h.point_count), reverse=True)
    hotspots = hotspots[:6]
    for rank, spot in enumerate(hotspots, start=1):
        spot.rank = rank

    failed: list[str] = []
    if gates.require_manifold and not mesh_rec.is_watertight:
        failed.append("reconstructed_not_watertight")
    if max_bbox_err > gates.max_bbox_error_mm:
        failed.append(f"bbox_error {max_bbox_err:.3f}mm > {gates.max_bbox_error_mm}mm")
    if vol_err_pct > gates.max_volume_error_pct:
        failed.append(f"volume_error {vol_err_pct:.2f}% > {gates.max_volume_error_pct}%")
    if iou < gates.min_volumetric_iou:
        failed.append(f"volumetric_iou {iou:.4f} < {gates.min_volumetric_iou}")
    if chamfer_mean > gates.max_chamfer_mm:
        failed.append(f"chamfer_mean {chamfer_mean:.3f}mm > {gates.max_chamfer_mm}mm")
    if hausdorff_95 > gates.max_hausdorff_95_mm:
        failed.append(f"hausdorff_95 {hausdorff_95:.3f}mm > {gates.max_hausdorff_95_mm}mm")

    report = EvalReport(
        input_stl=str(input_stl),
        reconstructed_stl=str(reconstructed_stl),
        reconstructed_watertight=bool(mesh_rec.is_watertight),
        bbox_delta_mm=[round(float(v), 4) for v in bbox_delta],
        max_bbox_error_mm=round(max_bbox_err, 4),
        centroid_offset_mm=[round(float(v), 4) for v in centroid_offset],
        input_volume_mm3=round(vol_in, 4),
        reconstructed_volume_mm3=round(vol_rec, 4),
        volume_error_pct=round(vol_err_pct, 4),
        surface_area_error_pct=round(area_err_pct, 4),
        volumetric_iou=round(iou, 5),
        iou_method=iou_method,  # type: ignore[arg-type]
        chamfer_mean_mm=round(chamfer_mean, 4),
        hausdorff_95_mm=round(hausdorff_95, 4),
        hausdorff_max_mm=round(hausdorff_max, 4),
        hotspots=hotspots,
        passed_gates=len(failed) == 0,
        failed_checks=failed,
    )
    (out_dir / "eval_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
