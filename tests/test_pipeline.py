"""Unit and integration tests for the stl2scad pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from stl2scad.evaluate_diff import evaluate_diff
from stl2scad.extract_features import extract_features
from stl2scad.inspect_stl import inspect_stl
from stl2scad.report import append_iteration, generate_report
from stl2scad.validate_slicer import validate_printability

REFS_DIR = Path(__file__).resolve().parents[1] / "References"


def _make_box_stl(path: Path, extents: tuple[float, float, float] = (40.0, 20.0, 10.0), offset: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Path:
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation([extents[0] / 2 + offset[0], extents[1] / 2 + offset[1], extents[2] / 2 + offset[2]])
    mesh.export(str(path))
    return path


def test_inspect_and_extract_box(tmp_path: Path) -> None:
    stl = _make_box_stl(tmp_path / "box.stl", (40.0, 20.0, 10.0))
    analysis = inspect_stl(stl, tmp_path / "out")

    assert analysis.watertight is True
    assert analysis.shell_count == 1
    assert analysis.non_manifold_edges == 0
    assert analysis.organic_warning is False
    assert analysis.organic_score < 0.1
    assert analysis.symmetry.mirror_x is True
    assert analysis.symmetry.mirror_y is True
    assert np.allclose(analysis.bbox.extents, [40.0, 20.0, 10.0], atol=0.05)

    feats = extract_features(stl, tmp_path / "out", save_plots=False)
    assert len(feats.slices) >= 1
    assert any(c.shape_type == "rect" for s in feats.slices for c in s.contours)


def test_inspect_multi_shell(tmp_path: Path) -> None:
    b1 = trimesh.creation.box(extents=(20.0, 20.0, 10.0))
    b2 = trimesh.creation.box(extents=(10.0, 10.0, 5.0))
    b2.apply_translation([35.0, 0.0, 0.0])
    combined = trimesh.util.concatenate([b1, b2])
    stl = tmp_path / "two_parts.stl"
    combined.export(str(stl))

    analysis = inspect_stl(stl, tmp_path / "out")
    assert analysis.shell_count == 2
    assert (tmp_path / "out" / "shells" / "part_0.stl").is_file()
    assert (tmp_path / "out" / "shells" / "part_1.stl").is_file()


def test_evaluate_diff_identical_and_offset(tmp_path: Path) -> None:
    stl_a = _make_box_stl(tmp_path / "a.stl", (30.0, 20.0, 10.0))
    stl_b = _make_box_stl(tmp_path / "b.stl", (30.0, 20.0, 10.0))
    stl_c = _make_box_stl(tmp_path / "c.stl", (35.0, 20.0, 10.0))

    rep_same = evaluate_diff(stl_a, stl_b, tmp_path / "eval_same", sample_count=4000)
    assert rep_same.passed_gates is True
    assert rep_same.volumetric_iou >= 0.999
    assert rep_same.volume_error_pct < 0.01
    assert rep_same.max_bbox_error_mm < 0.01

    rep_diff = evaluate_diff(stl_a, stl_c, tmp_path / "eval_diff", sample_count=4000)
    assert rep_diff.passed_gates is False
    assert rep_diff.max_bbox_error_mm == pytest.approx(5.0, abs=0.05)
    assert any(h.kind == "excess" for h in rep_diff.hotspots)


def test_validate_printability_builtin(tmp_path: Path) -> None:
    stl = _make_box_stl(tmp_path / "box.stl", (20.0, 20.0, 10.0))
    val = validate_printability(stl, stl, tmp_path / "val")
    assert val.passed is True
    assert val.watertight is True
    assert val.non_manifold_edges == 0
    assert val.bed_contact_area_mm2 == pytest.approx(400.0, abs=1.0)
    assert val.overhang_ratio_pct == 0.0


@pytest.mark.skipif(not (REFS_DIR / "kleinteil-schale" / "model.stl").is_file(), reason="Reference STLs not present")
def test_reference_kleinteil_schale(tmp_path: Path) -> None:
    stl = REFS_DIR / "kleinteil-schale" / "model.stl"
    analysis = inspect_stl(stl, tmp_path)
    feats = extract_features(stl, tmp_path, save_plots=False)

    assert analysis.watertight is True
    assert analysis.organic_warning is False
    assert np.allclose(analysis.bbox.extents, [120.0, 80.0, 25.0], atol=0.3)
    assert any(g.cols == 3 and g.rows == 2 for g in feats.grid_patterns)


@pytest.mark.skipif(not (REFS_DIR / "m6-clamping-knob" / "gemini" / "model.stl").is_file(), reason="Reference STLs not present")
def test_reference_m6_knob(tmp_path: Path) -> None:
    stl = REFS_DIR / "m6-clamping-knob" / "gemini" / "model.stl"
    analysis = inspect_stl(stl, tmp_path)
    feats = extract_features(stl, tmp_path, save_plots=False)

    assert analysis.watertight is True
    assert analysis.organic_warning is False
    assert any(p.lobe_count == 5 for p in feats.polar_patterns)


@pytest.mark.skipif(not (REFS_DIR / "gopro-battery-box" / "model.stl").is_file(), reason="Reference STLs not present")
def test_reference_gopro_box(tmp_path: Path) -> None:
    stl = REFS_DIR / "gopro-battery-box" / "model.stl"
    analysis = inspect_stl(stl, tmp_path)

    assert analysis.shell_count >= 2
    assert analysis.organic_warning is False


def test_generation_report(tmp_path: Path) -> None:
    stl_a = _make_box_stl(tmp_path / "a.stl", (30.0, 20.0, 10.0))
    stl_b = _make_box_stl(tmp_path / "b.stl", (32.0, 20.0, 10.0))
    scad = tmp_path / "model.scad"
    scad.write_text("cube([30, 20, 10]);\n", encoding="utf-8")

    out = tmp_path / "job"
    inspect_stl(stl_a, out)
    ev1 = evaluate_diff(stl_a, stl_b, out, sample_count=2000)
    val1 = validate_printability(stl_a, stl_b, out)
    append_iteration(out, ev1, val1, note="Initial rough box (+2mm X)")

    ev2 = evaluate_diff(stl_a, stl_a, out, sample_count=2000)
    val2 = validate_printability(stl_a, stl_a, out)
    hist = append_iteration(out, ev2, val2, note="Corrected X width to 30mm")
    assert len(hist) == 2

    rep, md_path = generate_report(
        out_dir=out,
        scad_file=scad,
        input_stl=stl_a,
        summary="Simple 30x20x10mm test block.",
        assumptions=["Assumed axis-aligned rectangular prism."],
        challenges=["Initial width overestimated by 2mm."],
    )
    assert rep.total_iterations == 2
    assert rep.passed_gates is True
    assert md_path.is_file()
    md_text = md_path.read_text(encoding="utf-8")
    assert "Iteration History" in md_text
    assert "Assumed axis-aligned rectangular prism." in md_text
    assert "Initial width overestimated by 2mm." in md_text

