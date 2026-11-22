"""Command-line interface for stl2scad."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from stl2scad.config import SlicerKind, find_openscad, find_slicer
from stl2scad.evaluate_diff import evaluate_diff
from stl2scad.extract_features import extract_features
from stl2scad.inspect_stl import inspect_stl
from stl2scad.render_compare import render_and_compare, render_input_previews
from stl2scad.report import append_iteration, generate_report
from stl2scad.validate_slicer import validate_printability

app = typer.Typer(
    name="stl2scad",
    help="Hybrid STL-to-OpenSCAD reverse-engineering pipeline.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def doctor() -> None:
    """Check availability of OpenSCAD and slicer CLIs (CuraEngine, PrusaSlicer, OrcaSlicer)."""
    scad = find_openscad()
    slicer = find_slicer()
    table = Table(title="stl2scad Environment")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Path")
    table.add_row("OpenSCAD", "[green]found[/green]" if scad else "[red]missing[/red]", str(scad or "-"))
    if slicer:
        table.add_row(f"Slicer ({slicer[0]})", "[green]found[/green]", str(slicer[1]))
    else:
        table.add_row("Slicer (Cura/Prusa/Orca)", "[yellow]not found (optional)[/yellow]", "-")
    console.print(table)


@app.command()
def inspect(
    stl: Path = typer.Argument(..., exists=True, readable=True, help="Input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
) -> None:
    """Stage 1: Inspect mesh health, bounding box, shells, symmetry, and organic score."""
    rep = inspect_stl(stl, out)
    console.print(
        f"[bold]Inspected:[/bold] {stl.name} | faces={rep.faces} watertight={rep.watertight} "
        f"shells={rep.shell_count} extents={rep.bbox.extents} organic={rep.organic_score:.2f}"
    )
    if rep.organic_warning:
        console.print("[yellow]WARNING:[/yellow] High organic/freeform score — skipping parametric CSG recommended.")
    console.print(f"Saved: {out / 'analysis.json'}")


@app.command()
def extract(
    stl: Path = typer.Argument(..., exists=True, readable=True, help="Input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
    no_plots: bool = typer.Option(False, "--no-plots", help="Skip saving 2D slice PNG plots."),
) -> None:
    """Stage 2: Extract Z-transitions, 2D contours, planes, grids, and polar patterns."""
    rep = extract_features(stl, out, save_plots=not no_plots)
    console.print(
        f"[bold]Extracted:[/bold] {len(rep.slices)} slices, {len(rep.planes)} planes, "
        f"{len(rep.grid_patterns)} grids, {len(rep.polar_patterns)} polar patterns."
    )
    console.print(f"Saved: {out / 'features.json'}")


@app.command()
def analyze(
    stl: Path = typer.Argument(..., exists=True, readable=True, help="Input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
    render_previews: bool = typer.Option(False, "--render-previews", help="Also render input STL views via OpenSCAD."),
    no_plots: bool = typer.Option(False, "--no-plots", help="Skip saving 2D slice PNG plots."),
) -> None:
    """Run Stage 1 (inspect) + Stage 2 (extract) [+ optional input previews]."""
    rep1 = inspect_stl(stl, out)
    if rep1.organic_warning:
        console.print(
            f"[yellow]Organic score {rep1.organic_score:.2f} exceeds threshold — skipping feature extraction.[/yellow]"
        )
        raise typer.Exit(code=2)
    rep2 = extract_features(stl, out, save_plots=not no_plots)
    if render_previews:
        views = render_input_previews(stl, out)
        console.print(f"Rendered {len(views)} input preview views.")
    console.print(
        f"[green]Analysis complete[/green]: shells={rep1.shell_count}, slices={len(rep2.slices)}, "
        f"grids={len(rep2.grid_patterns)}, polar={len(rep2.polar_patterns)} -> {out}"
    )


@app.command()
def render(
    scad: Path = typer.Argument(..., exists=True, readable=True, help="Reconstructed OpenSCAD file."),
    input_stl: Path = typer.Argument(..., exists=True, readable=True, help="Original input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
) -> None:
    """Stage 4: Compile .scad with OpenSCAD Manifold backend and render visual diff composites."""
    rep = render_and_compare(scad, input_stl, out)
    if rep.compile_exit_code != 0:
        console.print(f"[red]OpenSCAD compile failed (exit={rep.compile_exit_code}):[/red] {rep.errors or rep.compile_stderr}")
        raise typer.Exit(code=1)
    console.print(f"[green]Rendered[/green] {rep.reconstructed_stl} + {len(rep.diff_images)} diff composites.")


@app.command()
def evaluate(
    input_stl: Path = typer.Argument(..., exists=True, readable=True, help="Original input STL file."),
    reconstructed_stl: Path = typer.Argument(..., exists=True, readable=True, help="Reconstructed STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
) -> None:
    """Stage 5: Compute IoU, Chamfer, Hausdorff, and deviation hotspots."""
    rep = evaluate_diff(input_stl, reconstructed_stl, out)
    status = "[green]PASS[/green]" if rep.passed_gates else "[red]FAIL[/red]"
    console.print(
        f"{status} | IoU={rep.volumetric_iou:.4f} ({rep.iou_method}) | "
        f"VolErr={rep.volume_error_pct:.2f}% | BBoxErr={rep.max_bbox_error_mm:.3f}mm | "
        f"Chamfer={rep.chamfer_mean_mm:.3f}mm | H95={rep.hausdorff_95_mm:.3f}mm | "
        f"Hotspots={len(rep.hotspots)}"
    )
    if not rep.passed_gates:
        console.print(f"Failed checks: {', '.join(rep.failed_checks)}")


@app.command()
def validate(
    input_stl: Path = typer.Argument(..., exists=True, readable=True, help="Original input STL file."),
    reconstructed_stl: Path = typer.Argument(..., exists=True, readable=True, help="Reconstructed STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
    slicer: Optional[SlicerKind] = typer.Option(None, "--slicer", help="Preferred slicer: cura, prusa, orca."),
) -> None:
    """Stage 6: Validate manifoldness, bed contact, overhangs, and optional Cura/Prusa/Orca slicing."""
    rep = validate_printability(input_stl, reconstructed_stl, out, preferred_slicer=slicer)
    status = "[green]PASS[/green]" if rep.passed else "[red]FAIL[/red]"
    console.print(
        f"{status} | watertight={rep.watertight} non_manifold={rep.non_manifold_edges} "
        f"bed={rep.bed_contact_area_mm2:.1f}mm² overhang>45°={rep.overhang_ratio_pct:.1f}% "
        f"slicer={rep.slicer_status}"
    )


@app.command()
def verify(
    scad: Path = typer.Argument(..., exists=True, readable=True, help="Reconstructed OpenSCAD file."),
    input_stl: Path = typer.Argument(..., exists=True, readable=True, help="Original input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Output directory."),
    slicer: Optional[SlicerKind] = typer.Option(None, "--slicer", help="Preferred slicer: cura, prusa, orca."),
    track: bool = typer.Option(False, "--track", help="Record this run in iteration_history.json."),
    note: str = typer.Option("", "--note", help="Optional note for this iteration (used with --track)."),
) -> None:
    """Run Stage 4 (render) + Stage 5 (evaluate) + Stage 6 (validate) in one pass."""
    r_rep = render_and_compare(scad, input_stl, out)
    if r_rep.compile_exit_code != 0 or not r_rep.reconstructed_stl:
        console.print(f"[red]Compile failed (exit={r_rep.compile_exit_code})[/red]")
        raise typer.Exit(code=1)
    recon = Path(r_rep.reconstructed_stl)
    e_rep = evaluate_diff(input_stl, recon, out)
    v_rep = validate_printability(input_stl, recon, out, preferred_slicer=slicer)

    if track:
        hist = append_iteration(out, e_rep, v_rep, note=note)
        console.print(f"Recorded iteration #{len(hist)} in {out / 'iteration_history.json'}")

    overall = e_rep.passed_gates and v_rep.passed
    tag = "[green]ALL GATES PASSED[/green]" if overall else "[yellow]NEEDS REFINEMENT[/yellow]"
    console.print(
        f"{tag} | IoU={e_rep.volumetric_iou:.4f} | VolErr={e_rep.volume_error_pct:.2f}% | "
        f"Chamfer={e_rep.chamfer_mean_mm:.3f}mm | H95={e_rep.hausdorff_95_mm:.3f}mm | "
        f"Manifold={v_rep.watertight} | Slicer={v_rep.slicer_status}"
    )
    if not overall:
        raise typer.Exit(code=3)


@app.command()
def report(
    scad: Path = typer.Argument(..., exists=True, readable=True, help="Reconstructed OpenSCAD file."),
    input_stl: Path = typer.Argument(..., exists=True, readable=True, help="Original input STL file."),
    out: Path = typer.Option(Path("build/job"), "--out", "-o", help="Job output directory."),
    summary: str = typer.Option("", "--summary", help="High-level summary of the reconstructed design."),
    assumption: Optional[list[str]] = typer.Option(None, "--assumption", "-a", help="Modeling assumption (repeatable)."),
    challenge: Optional[list[str]] = typer.Option(None, "--challenge", "-c", help="Challenge or trade-off (repeatable)."),
) -> None:
    """Generate optional Markdown + JSON generation report (iterations, assumptions, challenges, metrics)."""
    rep, md_path = generate_report(
        out_dir=out,
        scad_file=scad,
        input_stl=input_stl,
        summary=summary,
        assumptions=assumption or [],
        challenges=challenge or [],
    )
    console.print(
        f"[green]Generation report written:[/green] {md_path} "
        f"(iterations={rep.total_iterations}, passed={rep.passed_gates})"
    )


@app.command()
def benchmark(
    refs: Path = typer.Option(Path("References"), "--refs", help="Directory containing reference STL models."),
    out: Path = typer.Option(Path("build/benchmark"), "--out", "-o", help="Output directory."),
) -> None:
    """Analyze all reference STL models under --refs and display a summary table."""
    stl_files = sorted(p for p in refs.rglob("model.stl"))
    if not stl_files:
        console.print(f"[yellow]No model.stl files found under {refs}[/yellow]")
        raise typer.Exit(code=1)

    table = Table(title="stl2scad Reference Benchmark (Analysis)")
    table.add_column("Model")
    table.add_column("Shells", justify="right")
    table.add_column("Extents (mm)")
    table.add_column("Symmetry")
    table.add_column("Organic", justify="right")
    table.add_column("Slices", justify="right")
    table.add_column("Patterns")

    for stl in stl_files:
        name = stl.parent.name if stl.parent.name != "gemini" else stl.parent.parent.name
        job_dir = out / name
        a = inspect_stl(stl, job_dir)
        f = extract_features(stl, job_dir, save_plots=True)
        sym_parts = []
        if a.symmetry.mirror_x:
            sym_parts.append("X")
        if a.symmetry.mirror_y:
            sym_parts.append("Y")
        if a.symmetry.rotational_order:
            sym_parts.append(f"R{a.symmetry.rotational_order}")
        pats = []
        for g in f.grid_patterns:
            pats.append(f"grid {g.cols}x{g.rows}")
        for p in f.polar_patterns:
            pats.append(f"polar {p.lobe_count}-lobe")
        ext_str = " x ".join(f"{v:.1f}" for v in a.bbox.extents)
        table.add_row(
            name,
            str(a.shell_count),
            ext_str,
            ",".join(sym_parts) or "-",
            f"{a.organic_score:.2f}",
            str(len(f.slices)),
            ", ".join(pats) or "-",
        )
    console.print(table)


if __name__ == "__main__":
    app()
