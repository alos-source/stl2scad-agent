"""Optional generation report builder: tracks iterations and renders Markdown + JSON reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stl2scad.schemas import (
    AnalysisReport,
    EvalReport,
    GenerationReport,
    IterationRecord,
    ValidationReport,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_iteration_history(out_dir: Path) -> list[IterationRecord]:
    hist_path = Path(out_dir) / "iteration_history.json"
    if not hist_path.is_file():
        return []
    raw = json.loads(hist_path.read_text(encoding="utf-8"))
    return [IterationRecord.model_validate(item) for item in raw]


def append_iteration(
    out_dir: Path,
    eval_report: EvalReport,
    validation_report: ValidationReport | None = None,
    note: str = "",
) -> list[IterationRecord]:
    """Append a verification iteration snapshot to iteration_history.json."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    history = load_iteration_history(out_dir)

    rec = IterationRecord(
        iteration=len(history) + 1,
        timestamp=_now_iso(),
        passed_gates=eval_report.passed_gates and (validation_report.passed if validation_report else True),
        volumetric_iou=eval_report.volumetric_iou,
        volume_error_pct=eval_report.volume_error_pct,
        max_bbox_error_mm=eval_report.max_bbox_error_mm,
        chamfer_mean_mm=eval_report.chamfer_mean_mm,
        hausdorff_95_mm=eval_report.hausdorff_95_mm,
        hausdorff_max_mm=eval_report.hausdorff_max_mm,
        hotspot_count=len(eval_report.hotspots),
        watertight=eval_report.reconstructed_watertight,
        note=note,
    )
    history.append(rec)
    (out_dir / "iteration_history.json").write_text(
        json.dumps([h.model_dump() for h in history], indent=2),
        encoding="utf-8",
    )
    return history


def _render_markdown(
    report: GenerationReport,
    analysis: AnalysisReport | None,
    out_dir: Path,
) -> str:
    lines: list[str] = []
    status_badge = "PASS" if report.passed_gates else "NEEDS REFINEMENT"
    lines.append(f"# Generation Report: `{report.project}`")
    lines.append("")
    lines.append(f"- **Generated:** `{report.generated_at}`")
    lines.append(f"- **Source STL:** `{report.input_stl}`")
    lines.append(f"- **Output SCAD:** `{report.output_scad}`")
    lines.append(f"- **Iterations:** `{report.total_iterations}`")
    lines.append(f"- **Status:** **{status_badge}**")
    lines.append("")

    if report.summary:
        lines.append("## Summary & Design Intent")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    if report.assumptions:
        lines.append("## Key Assumptions & Modeling Decisions")
        lines.append("")
        for item in report.assumptions:
            lines.append(f"- {item}")
        lines.append("")

    if report.challenges:
        lines.append("## Challenges & Trade-offs")
        lines.append("")
        for item in report.challenges:
            lines.append(f"- {item}")
        lines.append("")

    if report.iterations:
        lines.append("## Iteration History")
        lines.append("")
        lines.append("| Iter | Status | IoU | Vol Err | BBox Err | Chamfer | H95 | Max H | Note |")
        lines.append("| ---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |")
        for it in report.iterations:
            st = "PASS" if it.passed_gates else "FAIL"
            note_clean = it.note.replace("|", "\\|") if it.note else "-"
            lines.append(
                f"| {it.iteration} | {st} | {it.volumetric_iou:.4f} | {it.volume_error_pct:.2f}% | "
                f"{it.max_bbox_error_mm:.3f} mm | {it.chamfer_mean_mm:.3f} mm | "
                f"{it.hausdorff_95_mm:.3f} mm | {it.hausdorff_max_mm:.3f} mm | {note_clean} |"
            )
        lines.append("")

    if report.final_eval:
        ev = report.final_eval
        lines.append("## Final Geometric Evaluation")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| :--- | :--- |")
        lines.append(f"| Volumetric IoU (`{ev.iou_method}`) | `{ev.volumetric_iou:.5f}` |")
        lines.append(f"| Volume Error | `{ev.volume_error_pct:.3f}%` (`{ev.reconstructed_volume_mm3:.1f}` vs `{ev.input_volume_mm3:.1f} mm³`) |")
        lines.append(f"| Surface Area Error | `{ev.surface_area_error_pct:.3f}%` |")
        lines.append(f"| Max Bounding Box Error | `{ev.max_bbox_error_mm:.4f} mm` |")
        lines.append(f"| Mean Chamfer Distance | `{ev.chamfer_mean_mm:.4f} mm` |")
        lines.append(f"| Hausdorff 95% / Max | `{ev.hausdorff_95_mm:.4f} mm` / `{ev.hausdorff_max_mm:.4f} mm` |")
        lines.append(f"| Watertight | `{ev.reconstructed_watertight}` |")
        lines.append("")

    if report.final_validation:
        val = report.final_validation
        lines.append("## Printability & Slicer Validation")
        lines.append("")
        lines.append(f"- **Manifold / Watertight:** `{val.watertight}` (`{val.non_manifold_edges}` non-manifold edges)")
        lines.append(f"- **Bed Contact Area:** `{val.bed_contact_area_mm2:.2f} mm²`")
        lines.append(f"- **Overhang > 45°:** `{val.overhang_ratio_pct:.2f}%` (`{val.overhang_area_gt45_mm2:.2f} mm²`)")
        lines.append(f"- **External Slicer:** `{val.slicer_status}`")
        lines.append("")

    if analysis and analysis.notes:
        lines.append("## Input Mesh Diagnostics")
        lines.append("")
        for n in analysis.notes:
            lines.append(f"- {n}")
        lines.append("")

    diff_pngs = sorted(out_dir.glob("diff_*.png"))
    if diff_pngs:
        lines.append("## Visual Diffs")
        lines.append("")
        for p in diff_pngs:
            lines.append(f"- `{p.name}`")
        lines.append("")

    return "\n".join(lines)


def generate_report(
    out_dir: Path,
    scad_file: Path,
    input_stl: Path,
    summary: str = "",
    assumptions: list[str] | None = None,
    challenges: list[str] | None = None,
) -> tuple[GenerationReport, Path]:
    """Assemble generation_report.json and generation_report.md from stage outputs and agent notes."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scad_file = Path(scad_file).resolve()
    input_stl = Path(input_stl).resolve()

    analysis: AnalysisReport | None = None
    if (out_dir / "analysis.json").is_file():
        analysis = AnalysisReport.model_validate_json((out_dir / "analysis.json").read_text(encoding="utf-8"))

    eval_rep: EvalReport | None = None
    if (out_dir / "eval_report.json").is_file():
        eval_rep = EvalReport.model_validate_json((out_dir / "eval_report.json").read_text(encoding="utf-8"))

    val_rep: ValidationReport | None = None
    if (out_dir / "validation_report.json").is_file():
        val_rep = ValidationReport.model_validate_json((out_dir / "validation_report.json").read_text(encoding="utf-8"))

    history = load_iteration_history(out_dir)
    if not history and eval_rep is not None:
        history = [
            IterationRecord(
                iteration=1,
                timestamp=_now_iso(),
                passed_gates=eval_rep.passed_gates and (val_rep.passed if val_rep else True),
                volumetric_iou=eval_rep.volumetric_iou,
                volume_error_pct=eval_rep.volume_error_pct,
                max_bbox_error_mm=eval_rep.max_bbox_error_mm,
                chamfer_mean_mm=eval_rep.chamfer_mean_mm,
                hausdorff_95_mm=eval_rep.hausdorff_95_mm,
                hausdorff_max_mm=eval_rep.hausdorff_max_mm,
                hotspot_count=len(eval_rep.hotspots),
                watertight=eval_rep.reconstructed_watertight,
                note="Final snapshot (iteration tracking was not enabled during verify)",
            )
        ]

    passed = (eval_rep.passed_gates if eval_rep else False) and (val_rep.passed if val_rep else True)
    report = GenerationReport(
        project=scad_file.stem,
        generated_at=_now_iso(),
        input_stl=str(input_stl),
        output_scad=str(scad_file),
        total_iterations=len(history),
        passed_gates=passed,
        summary=summary,
        assumptions=assumptions or [],
        challenges=challenges or [],
        iterations=history,
        final_eval=eval_rep,
        final_validation=val_rep,
    )

    (out_dir / "generation_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path = out_dir / "generation_report.md"
    md_path.write_text(_render_markdown(report, analysis, out_dir), encoding="utf-8")
    return report, md_path
