"""Typed JSON schemas for inter-stage communication in stl2scad."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BBoxInfo(BaseModel):
    min: list[float] = Field(..., min_length=3, max_length=3)
    max: list[float] = Field(..., min_length=3, max_length=3)
    extents: list[float] = Field(..., min_length=3, max_length=3)
    center: list[float] = Field(..., min_length=3, max_length=3)


class ShellSummary(BaseModel):
    index: int
    file: str | None = None
    faces: int
    vertices: int
    watertight: bool
    volume_mm3: float
    surface_area_mm2: float
    bbox: BBoxInfo


class SymmetryInfo(BaseModel):
    mirror_x: bool = False
    mirror_y: bool = False
    rotational_order: int | None = None
    rotational_error_mm: float | None = None


class AnalysisReport(BaseModel):
    input_file: str
    faces: int
    vertices: int
    watertight: bool
    is_winding_consistent: bool
    euler_number: int
    non_manifold_edges: int
    volume_mm3: float
    surface_area_mm2: float
    bbox: BBoxInfo
    bed_contact_area_mm2: float
    bed_contact_z: float
    shell_count: int
    shells: list[ShellSummary]
    symmetry: SymmetryInfo
    organic_score: float = Field(..., ge=0.0, le=1.0)
    organic_warning: bool = False
    notes: list[str] = Field(default_factory=list)


class Contour2D(BaseModel):
    is_hole: bool
    shape_type: Literal["circle", "hexagon", "rounded_rect", "rect", "polygon"]
    area_mm2: float
    perimeter_mm: float
    center_xy: list[float]
    extents_xy: list[float]
    radius_mm: float | None = None
    corner_radius_mm: float | None = None
    hex_wrench_size_mm: float | None = None
    circularity: float


class ZSliceSummary(BaseModel):
    z_mm: float
    z_band_start: float
    z_band_end: float
    total_area_mm2: float
    outer_count: int
    hole_count: int
    contours: list[Contour2D]
    plot_file: str | None = None


class PlaneCluster(BaseModel):
    kind: Literal["horizontal_up", "horizontal_down", "vertical", "chamfer_45", "sloped"]
    normal: list[float]
    offset_mm: float
    z_min: float
    z_max: float
    area_mm2: float
    face_count: int


class GridPattern(BaseModel):
    rows: int
    cols: int
    cell_width_mm: float
    cell_depth_mm: float
    pitch_x_mm: float
    pitch_y_mm: float
    wall_x_mm: float | None = None
    wall_y_mm: float | None = None
    z_mm: float


class PolarPattern(BaseModel):
    lobe_count: int
    center_xy: list[float]
    min_radius_mm: float
    max_radius_mm: float
    z_mm: float


class FeatureReport(BaseModel):
    input_file: str
    z_transitions_mm: list[float]
    slices: list[ZSliceSummary]
    planes: list[PlaneCluster]
    grid_patterns: list[GridPattern] = Field(default_factory=list)
    polar_patterns: list[PolarPattern] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RenderReport(BaseModel):
    scad_file: str
    input_stl: str
    reconstructed_stl: str | None = None
    openscad_bin: str | None = None
    compile_exit_code: int
    compile_stdout: str = ""
    compile_stderr: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    preview_images: dict[str, str] = Field(default_factory=dict)
    diff_images: dict[str, str] = Field(default_factory=dict)


class DeviationHotspot(BaseModel):
    rank: int
    kind: Literal["missing", "excess"]
    centroid_mm: list[float]
    max_error_mm: float
    mean_error_mm: float
    point_count: int


class EvalReport(BaseModel):
    input_stl: str
    reconstructed_stl: str
    reconstructed_watertight: bool
    bbox_delta_mm: list[float]
    max_bbox_error_mm: float
    centroid_offset_mm: list[float]
    input_volume_mm3: float
    reconstructed_volume_mm3: float
    volume_error_pct: float
    surface_area_error_pct: float
    volumetric_iou: float
    iou_method: Literal["manifold_boolean", "voxel"]
    chamfer_mean_mm: float
    hausdorff_95_mm: float
    hausdorff_max_mm: float
    hotspots: list[DeviationHotspot] = Field(default_factory=list)
    passed_gates: bool
    failed_checks: list[str] = Field(default_factory=list)


class SlicerMetrics(BaseModel):
    slicer: Literal["cura", "prusa", "orca"]
    slicer_bin: str
    status: Literal["ok", "failed"]
    input_layers: int | None = None
    reconstructed_layers: int | None = None
    input_filament_mm3: float | None = None
    reconstructed_filament_mm3: float | None = None
    notes: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    input_stl: str
    reconstructed_stl: str
    watertight: bool
    non_manifold_edges: int
    bed_contact_area_mm2: float
    overhang_area_gt45_mm2: float
    overhang_ratio_pct: float
    slicer_status: Literal["ok", "skipped_not_installed", "failed"]
    slicer_metrics: SlicerMetrics | None = None
    passed: bool
    notes: list[str] = Field(default_factory=list)


class IterationRecord(BaseModel):
    iteration: int
    timestamp: str
    passed_gates: bool
    volumetric_iou: float
    volume_error_pct: float
    max_bbox_error_mm: float
    chamfer_mean_mm: float
    hausdorff_95_mm: float
    hausdorff_max_mm: float
    hotspot_count: int
    watertight: bool
    note: str = ""


class GenerationReport(BaseModel):
    project: str
    generated_at: str
    input_stl: str
    output_scad: str
    total_iterations: int
    passed_gates: bool
    summary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)
    iterations: list[IterationRecord] = Field(default_factory=list)
    final_eval: EvalReport | None = None
    final_validation: ValidationReport | None = None

