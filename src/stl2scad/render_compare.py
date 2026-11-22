"""Stage 4: OpenSCAD CLI compilation, synchronized multi-view rendering, and visual diff composites."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from stl2scad.config import find_openscad
from stl2scad.inspect_stl import load_mesh
from stl2scad.schemas import RenderReport


def _compute_camera_args(center: np.ndarray, diag: float) -> dict[str, tuple[str, str]]:
    """Return view_name -> (camera_str, projection_flag) centered on the combined mesh."""
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    dist = max(20.0, diag * 2.2)
    return {
        "iso": (f"{cx:.2f},{cy:.2f},{cz:.2f},55,0,25,{dist:.2f}", "o"),
        "top": (f"{cx:.2f},{cy:.2f},{cz:.2f},0,0,0,{dist:.2f}", "o"),
        "bottom": (f"{cx:.2f},{cy:.2f},{cz:.2f},180,0,0,{dist:.2f}", "o"),
        "side": (f"{cx:.2f},{cy:.2f},{cz:.2f},90,0,0,{dist:.2f}", "o"),
    }


def _run_openscad(openscad_bin: Path, args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(openscad_bin), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _render_stl_views(
    openscad_bin: Path,
    stl_path: Path,
    out_dir: Path,
    prefix: str,
    cameras: dict[str, tuple[str, str]],
    imgsize: str = "640,480",
) -> dict[str, Path]:
    wrapper = out_dir / f"_{prefix}_wrapper.scad"
    stl_posix = stl_path.resolve().as_posix()
    wrapper.write_text(f'import("{stl_posix}");\n', encoding="utf-8")

    outputs: dict[str, Path] = {}
    for view, (cam, proj) in cameras.items():
        png_path = out_dir / f"{prefix}_{view}.png"
        proc = _run_openscad(
            openscad_bin,
            [
                f"--camera={cam}",
                f"--projection={proj}",
                f"--imgsize={imgsize}",
                "--colorscheme=Tomorrow Night",
                "-o",
                str(png_path),
                str(wrapper),
            ],
        )
        if proc.returncode == 0 and png_path.is_file():
            outputs[view] = png_path
    try:
        wrapper.unlink(missing_ok=True)
    except OSError:
        pass
    return outputs


def _make_diff_composite(orig_png: Path, recon_png: Path, out_png: Path, title: str) -> None:
    img_a = Image.open(orig_png).convert("RGB")
    img_b = Image.open(recon_png).convert("RGB")
    if img_a.size != img_b.size:
        img_b = img_b.resize(img_a.size)

    arr_a = np.asarray(img_a, dtype=np.int16)
    arr_b = np.asarray(img_b, dtype=np.int16)
    diff = np.abs(arr_a - arr_b).max(axis=2)
    # Highlight pixels that differ significantly in bright red on a dimmed grayscale base
    base = np.mean(arr_a, axis=2, keepdims=True) * 0.45
    diff_rgb = np.repeat(base, 3, axis=2).astype(np.uint8)
    mask = diff > 18
    diff_rgb[mask] = [239, 68, 68]
    img_diff = Image.fromarray(diff_rgb)

    w, h = img_a.size
    header_h = 26
    canvas = Image.new("RGB", (w * 3, h + header_h), (24, 24, 27))
    canvas.paste(img_a, (0, header_h))
    canvas.paste(img_b, (w, header_h))
    canvas.paste(img_diff, (w * 2, header_h))

    draw = ImageDraw.Draw(canvas)
    draw.text((10, 6), f"Original ({title})", fill=(228, 228, 231))
    draw.text((w + 10, 6), f"Reconstructed ({title})", fill=(228, 228, 231))
    draw.text((w * 2 + 10, 6), f"Pixel Diff ({title})", fill=(248, 113, 113))
    canvas.save(out_png)


def render_input_previews(input_stl: Path, out_dir: Path) -> dict[str, str]:
    """Render reference orthographic/iso previews of an input STL (no .scad needed yet)."""
    openscad_bin = find_openscad()
    if openscad_bin is None:
        return {}
    input_stl = Path(input_stl).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mesh = load_mesh(input_stl)
    center = (mesh.bounds[0] + mesh.bounds[1]) / 2.0
    diag = float(np.linalg.norm(mesh.extents))
    cameras = _compute_camera_args(center, diag)
    rendered = _render_stl_views(openscad_bin, input_stl, out_dir, "input_preview", cameras)
    return {k: str(v) for k, v in rendered.items()}


def render_and_compare(scad_file: Path, input_stl: Path, out_dir: Path) -> RenderReport:
    """Compile model.scad with OpenSCAD Manifold backend and render synchronized comparison views."""
    scad_file = Path(scad_file).resolve()
    input_stl = Path(input_stl).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    openscad_bin = find_openscad()
    if openscad_bin is None:
        report = RenderReport(
            scad_file=str(scad_file),
            input_stl=str(input_stl),
            openscad_bin=None,
            compile_exit_code=-1,
            errors=["OpenSCAD CLI binary not found. Set OPENSCAD_BIN or install OpenSCAD."],
        )
        (out_dir / "render_log.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report

    recon_stl = out_dir / "reconstructed.stl"
    proc = _run_openscad(
        openscad_bin,
        ["--backend=Manifold", "--export-format=binstl", "-o", str(recon_stl), str(scad_file)],
    )
    if proc.returncode != 0 and "unrecognised option '--backend=Manifold'" in (proc.stdout + proc.stderr):
        # Fallback for OpenSCAD 2021.01 (CGAL backend)
        proc = _run_openscad(
            openscad_bin,
            ["--export-format=binstl", "-o", str(recon_stl), str(scad_file)],
        )

    warnings = [line for line in proc.stderr.splitlines() if "WARNING:" in line.upper()]
    errors = [line for line in proc.stderr.splitlines() if "ERROR:" in line.upper()]

    preview_images: dict[str, str] = {}
    diff_images: dict[str, str] = {}

    if proc.returncode == 0 and recon_stl.is_file():
        mesh_in = load_mesh(input_stl)
        mesh_rec = load_mesh(recon_stl)
        bmin = np.minimum(mesh_in.bounds[0], mesh_rec.bounds[0])
        bmax = np.maximum(mesh_in.bounds[1], mesh_rec.bounds[1])
        center = (bmin + bmax) / 2.0
        diag = float(math.dist(bmin, bmax))
        cameras = _compute_camera_args(center, diag)

        in_views = _render_stl_views(openscad_bin, input_stl, out_dir, "input_preview", cameras)
        rec_views = _render_stl_views(openscad_bin, recon_stl, out_dir, "preview", cameras)
        for view, path in rec_views.items():
            preview_images[view] = str(path)
            if view in in_views:
                diff_path = out_dir / f"diff_{view}.png"
                _make_diff_composite(in_views[view], path, diff_path, view)
                diff_images[view] = str(diff_path)

    report = RenderReport(
        scad_file=str(scad_file),
        input_stl=str(input_stl),
        reconstructed_stl=str(recon_stl) if recon_stl.is_file() else None,
        openscad_bin=str(openscad_bin),
        compile_exit_code=int(proc.returncode),
        compile_stdout=proc.stdout.strip(),
        compile_stderr=proc.stderr.strip(),
        warnings=warnings,
        errors=errors,
        preview_images=preview_images,
        diff_images=diff_images,
    )
    (out_dir / "render_log.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
