"""Stage 2: Adaptive Z-slicing, 2D contour classification, plane clustering, and pattern detection."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import shapely.geometry as sgeom
import trimesh

from stl2scad.inspect_stl import load_mesh
from stl2scad.schemas import (
    Contour2D,
    FeatureReport,
    GridPattern,
    PlaneCluster,
    PolarPattern,
    ZSliceSummary,
)


def _cluster_planes(mesh: trimesh.Trimesh, min_area_ratio: float = 0.008) -> list[PlaneCluster]:
    total_area = float(mesh.area)
    if total_area <= 1e-9:
        return []
    min_area = total_area * min_area_ratio
    normals = mesh.face_normals
    areas = mesh.area_faces
    centroids = mesh.triangles_center
    tri_z = mesh.triangles[:, :, 2]

    # Quantize normal (0.05 step) + plane offset (0.15 mm step)
    offsets = np.einsum("ij,ij->i", normals, centroids)
    n_key = np.round(normals / 0.05).astype(int)
    d_key = np.round(offsets / 0.15).astype(int)
    keys = np.column_stack([n_key, d_key])

    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    clusters: list[PlaneCluster] = []
    for idx in range(len(uniq)):
        mask = inv == idx
        area = float(np.sum(areas[mask]))
        if area < min_area:
            continue
        mean_n = np.average(normals[mask], axis=0, weights=areas[mask])
        norm = np.linalg.norm(mean_n)
        if norm > 1e-9:
            mean_n = mean_n / norm
        mean_d = float(np.average(offsets[mask], weights=areas[mask]))
        nz = float(mean_n[2])
        if nz >= 0.98:
            kind = "horizontal_up"
        elif nz <= -0.98:
            kind = "horizontal_down"
        elif abs(nz) <= 0.05:
            kind = "vertical"
        elif abs(abs(nz) - math.sqrt(0.5)) <= 0.06:
            kind = "chamfer_45"
        else:
            kind = "sloped"
        clusters.append(
            PlaneCluster(
                kind=kind,
                normal=[round(float(v), 4) for v in mean_n],
                offset_mm=round(mean_d, 3),
                z_min=round(float(np.min(tri_z[mask])), 3),
                z_max=round(float(np.max(tri_z[mask])), 3),
                area_mm2=round(area, 3),
                face_count=int(np.sum(mask)),
            )
        )

    clusters.sort(key=lambda c: c.area_mm2, reverse=True)
    return clusters[:40]


def _classify_polygon(poly: sgeom.Polygon, is_hole: bool) -> Contour2D:
    area = float(poly.area)
    perim = float(poly.length)
    minx, miny, maxx, maxy = poly.bounds
    w, d = maxx - minx, maxy - miny
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    circularity = (4.0 * math.pi * area / (perim * perim)) if perim > 1e-9 else 0.0

    # Check circle
    radius: float | None = None
    corner_r: float | None = None
    hex_s: float | None = None
    shape_type: str = "polygon"

    if circularity >= 0.965 and abs(w - d) <= max(0.3, 0.05 * max(w, d)):
        shape_type = "circle"
        radius = round(math.sqrt(area / math.pi), 3)
    else:
        # Check regular hexagon (simplify tolerance scaled to size)
        simp = poly.simplify(tolerance=max(0.05, 0.01 * max(w, d)), preserve_topology=True)
        n_verts = len(simp.exterior.coords) - 1
        if n_verts == 6 and 0.87 <= circularity <= 0.94:
            # Width across flats for a regular hexagon: area = (sqrt(3)/2) * s^2
            s_est = math.sqrt(area / (math.sqrt(3.0) / 2.0))
            shape_type = "hexagon"
            hex_s = round(s_est, 3)
            radius = round(s_est / math.sqrt(3.0), 3)
        else:
            bbox_area = w * d
            fill_ratio = area / bbox_area if bbox_area > 1e-9 else 0.0
            if fill_ratio >= 0.985:
                shape_type = "rect"
            elif fill_ratio >= 0.82 and abs(poly.area - poly.minimum_rotated_rectangle.area) / max(area, 1e-9) <= 0.22:
                # Estimate rounded rectangle corner radius: missing area = (4 - pi) * r^2
                missing = max(0.0, bbox_area - area)
                r_est = math.sqrt(missing / (4.0 - math.pi))
                if 0.2 <= r_est <= min(w, d) / 2.0 + 0.2:
                    shape_type = "rounded_rect"
                    corner_r = round(r_est, 3)

    return Contour2D(
        is_hole=is_hole,
        shape_type=shape_type,  # type: ignore[arg-type]
        area_mm2=round(area, 3),
        perimeter_mm=round(perim, 3),
        center_xy=[round(cx, 3), round(cy, 3)],
        extents_xy=[round(w, 3), round(d, 3)],
        radius_mm=radius,
        corner_radius_mm=corner_r,
        hex_wrench_size_mm=hex_s,
        circularity=round(circularity, 4),
    )


def _slice_polygons(mesh: trimesh.Trimesh, z: float) -> list[sgeom.Polygon]:
    section = mesh.section(plane_origin=[0.0, 0.0, z], plane_normal=[0.0, 0.0, 1.0])
    if section is None:
        return []
    # Project directly onto the world XY plane preserving exact world (X, Y) coordinates
    path2d, _ = section.to_2D(to_2D=np.eye(4))
    polys = list(path2d.polygons_full)
    return [p for p in polys if p.is_valid and p.area > 0.05]


def _find_z_transitions(mesh: trimesh.Trimesh, planes: list[PlaneCluster], max_samples: int = 80) -> list[float]:
    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = z_max - z_min
    if height <= 1e-6:
        return [round(z_min, 3)]

    candidates: set[float] = {round(z_min, 3), round(z_max, 3)}
    for pl in planes:
        if pl.kind in ("horizontal_up", "horizontal_down"):
            candidates.add(round(pl.z_min, 3))
        elif pl.kind in ("chamfer_45", "sloped"):
            candidates.add(round(pl.z_min, 3))
            candidates.add(round(pl.z_max, 3))

    # Also sample evenly to catch topology changes without large horizontal faces
    n = min(max_samples, max(12, int(math.ceil(height / 0.75))))
    zs = np.linspace(z_min + 0.05, z_max - 0.05, n)
    prev_sig: tuple[int, int, float] | None = None
    for z in zs:
        polys = _slice_polygons(mesh, float(z))
        outer_n = len(polys)
        hole_n = sum(len(p.interiors) for p in polys)
        area = sum(p.area for p in polys)
        if prev_sig is not None:
            p_out, p_hole, p_area = prev_sig
            rel_area = abs(area - p_area) / max(p_area, 1.0)
            if outer_n != p_out or hole_n != p_hole or rel_area > 0.12:
                candidates.add(round(float(z), 3))
        prev_sig = (outer_n, hole_n, area)

    # Merge transitions closer than 0.4 mm
    sorted_z = sorted(candidates)
    merged: list[float] = []
    for z in sorted_z:
        if not merged or abs(z - merged[-1]) >= 0.4:
            merged.append(z)
    if abs(merged[-1] - z_max) >= 0.1:
        merged.append(round(z_max, 3))
    return merged


def _detect_grid_pattern(contours: list[Contour2D], z_mm: float) -> GridPattern | None:
    holes = [c for c in contours if c.is_hole and c.area_mm2 >= 4.0]
    if len(holes) < 4:
        return None
    # Group similar-sized holes
    areas = np.array([h.area_mm2 for h in holes])
    med_area = float(np.median(areas))
    similar = [h for h in holes if abs(h.area_mm2 - med_area) / max(med_area, 1.0) <= 0.15]
    if len(similar) < 4:
        return None

    xs = sorted(h.center_xy[0] for h in similar)
    ys = sorted(h.center_xy[1] for h in similar)

    def _cluster_1d(vals: list[float], tol: float = 1.0) -> list[float]:
        groups: list[list[float]] = []
        for v in vals:
            if not groups or abs(v - groups[-1][-1]) > tol:
                groups.append([v])
            else:
                groups[-1].append(v)
        return [float(np.mean(g)) for g in groups]

    col_centers = _cluster_1d(xs)
    row_centers = _cluster_1d(ys)
    cols, rows = len(col_centers), len(row_centers)
    if cols * rows != len(similar) or (cols < 2 and rows < 2):
        return None

    pitch_x = float(np.mean(np.diff(col_centers))) if cols > 1 else 0.0
    pitch_y = float(np.mean(np.diff(row_centers))) if rows > 1 else 0.0
    cell_w = float(np.median([h.extents_xy[0] for h in similar]))
    cell_d = float(np.median([h.extents_xy[1] for h in similar]))
    wall_x = round(pitch_x - cell_w, 3) if cols > 1 else None
    wall_y = round(pitch_y - cell_d, 3) if rows > 1 else None

    return GridPattern(
        rows=rows,
        cols=cols,
        cell_width_mm=round(cell_w, 3),
        cell_depth_mm=round(cell_d, 3),
        pitch_x_mm=round(pitch_x, 3),
        pitch_y_mm=round(pitch_y, 3),
        wall_x_mm=wall_x,
        wall_y_mm=wall_y,
        z_mm=round(z_mm, 3),
    )


def _detect_polar_pattern(polys: list[sgeom.Polygon], z_mm: float) -> PolarPattern | None:
    if len(polys) != 1:
        return None
    poly = polys[0]
    coords = np.asarray(poly.exterior.coords)
    if len(coords) < 24:
        return None
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    dx = coords[:, 0] - cx
    dy = coords[:, 1] - cy
    angles = np.arctan2(dy, dx)
    radii = np.hypot(dx, dy)

    r_min, r_max = float(np.min(radii)), float(np.max(radii))
    if (r_max - r_min) / max(r_max, 1.0) < 0.06:
        return None  # nearly circular, not lobed

    # Resample radius uniformly in angle [0, 2pi) and run FFT to find dominant lobe frequency
    order = np.argsort(angles)
    ang_sorted = angles[order]
    rad_sorted = radii[order]
    # Wrap around
    ang_ext = np.concatenate([ang_sorted - 2 * math.pi, ang_sorted, ang_sorted + 2 * math.pi])
    rad_ext = np.concatenate([rad_sorted, rad_sorted, rad_sorted])
    grid = np.linspace(-math.pi, math.pi, 360, endpoint=False)
    r_uniform = np.interp(grid, ang_ext, rad_ext)
    r_zero = r_uniform - np.mean(r_uniform)
    spectrum = np.abs(np.fft.rfft(r_zero))
    # Search lobe counts 3..12
    cand = spectrum[3:13]
    best_k = int(np.argmax(cand)) + 3
    if spectrum[best_k] < 0.45 * float(np.sum(cand)):
        return None

    return PolarPattern(
        lobe_count=best_k,
        center_xy=[round(cx, 3), round(cy, 3)],
        min_radius_mm=round(r_min, 3),
        max_radius_mm=round(r_max, 3),
        z_mm=round(z_mm, 3),
    )


def _save_slice_plot(polys: list[sgeom.Polygon], z_mm: float, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 5), dpi=120)
    for poly in polys:
        x, y = poly.exterior.xy
        ax.fill(x, y, alpha=0.25, fc="#2563eb", ec="#1e3a8a", lw=1.2)
        for interior in poly.interiors:
            hx, hy = interior.xy
            ax.fill(hx, hy, alpha=1.0, fc="white", ec="#dc2626", lw=1.0)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.set_title(f"Z = {z_mm:.2f} mm")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def extract_features(stl_path: Path, out_dir: Path, save_plots: bool = True) -> FeatureReport:
    """Run Stage 2 feature extraction and write features.json (+ slice PNG plots)."""
    stl_path = Path(stl_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slices_dir = out_dir / "slices"
    if save_plots:
        slices_dir.mkdir(parents=True, exist_ok=True)

    mesh = load_mesh(stl_path)
    planes = _cluster_planes(mesh)
    transitions = _find_z_transitions(mesh, planes)

    slice_summaries: list[ZSliceSummary] = []
    grid_patterns: list[GridPattern] = []
    polar_patterns: list[PolarPattern] = []

    for i in range(len(transitions) - 1):
        z0, z1 = transitions[i], transitions[i + 1]
        if z1 - z0 < 0.15:
            continue
        z_mid = round((z0 + z1) / 2.0, 3)
        polys = _slice_polygons(mesh, z_mid)
        if not polys:
            continue

        contours: list[Contour2D] = []
        for poly in polys:
            contours.append(_classify_polygon(poly, is_hole=False))
            for interior in poly.interiors:
                hole_poly = sgeom.Polygon(interior)
                if hole_poly.is_valid and hole_poly.area > 0.05:
                    contours.append(_classify_polygon(hole_poly, is_hole=True))

        plot_file: str | None = None
        if save_plots:
            p_path = slices_dir / f"z_{z_mid:06.2f}.png"
            _save_slice_plot(polys, z_mid, p_path)
            plot_file = str(p_path)

        total_area = sum(p.area for p in polys)
        slice_summaries.append(
            ZSliceSummary(
                z_mm=z_mid,
                z_band_start=round(z0, 3),
                z_band_end=round(z1, 3),
                total_area_mm2=round(total_area, 3),
                outer_count=len(polys),
                hole_count=sum(len(p.interiors) for p in polys),
                contours=contours,
                plot_file=plot_file,
            )
        )

        gp = _detect_grid_pattern(contours, z_mid)
        if gp and not any(g.rows == gp.rows and g.cols == gp.cols for g in grid_patterns):
            grid_patterns.append(gp)

        pp = _detect_polar_pattern(polys, z_mid)
        if pp and not any(p.lobe_count == pp.lobe_count for p in polar_patterns):
            polar_patterns.append(pp)

    report = FeatureReport(
        input_file=str(stl_path),
        z_transitions_mm=transitions,
        slices=slice_summaries,
        planes=planes,
        grid_patterns=grid_patterns,
        polar_patterns=polar_patterns,
    )
    (out_dir / "features.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
