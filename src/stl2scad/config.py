"""Configuration, quality gates, and external binary discovery."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

SlicerKind = Literal["cura", "prusa", "orca"]


class QualityGates(BaseModel):
    """Default acceptance thresholds for STL-to-OpenSCAD reconstruction."""

    max_bbox_error_mm: float = 0.20
    max_volume_error_pct: float = 2.0
    min_volumetric_iou: float = 0.96
    max_chamfer_mm: float = 0.20
    max_hausdorff_95_mm: float = 0.35
    require_manifold: bool = True
    organic_warn_threshold: float = 0.65


DEFAULT_GATES = QualityGates()


def _first_existing(candidates: list[str | Path | None]) -> Path | None:
    for item in candidates:
        if not item:
            continue
        p = Path(item)
        if p.is_file():
            return p
    return None


def _glob_candidates(patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for match in sorted(glob.glob(pattern), reverse=True):
            p = Path(match)
            if p.is_file():
                found.append(p)
    return found


def find_openscad() -> Path | None:
    """Locate the OpenSCAD CLI binary (preferring openscad.com on Windows)."""
    env_bin = os.environ.get("OPENSCAD_BIN")
    direct = _first_existing(
        [
            env_bin,
            shutil.which("openscad.com"),
            shutil.which("openscad"),
            r"C:\Program Files\OpenSCAD\openscad.com",
            r"C:\Program Files\OpenSCAD\openscad.exe",
            r"C:\Program Files (x86)\OpenSCAD\openscad.com",
            r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
            "/usr/bin/openscad",
            "/opt/homebrew/bin/openscad",
        ]
    )
    return direct


def find_slicer(preferred: SlicerKind | None = None) -> tuple[SlicerKind, Path] | None:
    """Locate CuraEngine, PrusaSlicer, or OrcaSlicer CLI binary."""
    local_app = os.environ.get("LOCALAPPDATA", "")

    def _find_cura() -> Path | None:
        direct = _first_existing(
            [
                os.environ.get("CURAENGINE_BIN"),
                shutil.which("CuraEngine"),
                shutil.which("CuraEngine.exe"),
            ]
        )
        if direct:
            return direct
        patterns = [
            r"C:\Program Files\UltiMaker Cura*\CuraEngine.exe",
            r"C:\Program Files\Ultimaker Cura*\CuraEngine.exe",
            r"C:\Program Files\Cura*\CuraEngine.exe",
        ]
        if local_app:
            patterns.append(os.path.join(local_app, "Programs", "*Cura*", "CuraEngine.exe"))
        matches = _glob_candidates(patterns)
        return matches[0] if matches else None

    def _find_prusa() -> Path | None:
        direct = _first_existing(
            [
                os.environ.get("PRUSASLICER_BIN"),
                shutil.which("prusa-slicer-console"),
                shutil.which("prusa-slicer"),
                r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
                r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
            ]
        )
        if direct:
            return direct
        patterns = [r"C:\Program Files\Prusa3D\*\prusa-slicer-console.exe"]
        if local_app:
            patterns.append(os.path.join(local_app, "Programs", "*Prusa*", "prusa-slicer-console.exe"))
        matches = _glob_candidates(patterns)
        return matches[0] if matches else None

    def _find_orca() -> Path | None:
        direct = _first_existing(
            [
                os.environ.get("ORCASLICER_BIN"),
                shutil.which("orca-slicer"),
                r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
            ]
        )
        if direct:
            return direct
        patterns = [r"C:\Program Files\OrcaSlicer*\orca-slicer.exe"]
        if local_app:
            patterns.append(os.path.join(local_app, "Programs", "*Orca*", "orca-slicer.exe"))
        matches = _glob_candidates(patterns)
        return matches[0] if matches else None

    # Generic override
    generic = os.environ.get("SLICER_BIN")
    if generic and Path(generic).is_file():
        name_l = Path(generic).name.lower()
        if "prusa" in name_l:
            return ("prusa", Path(generic))
        if "orca" in name_l:
            return ("orca", Path(generic))
        return ("cura", Path(generic))

    order: list[tuple[SlicerKind, callable]] = [
        ("cura", _find_cura),
        ("prusa", _find_prusa),
        ("orca", _find_orca),
    ]
    if preferred:
        order.sort(key=lambda pair: 0 if pair[0] == preferred else 1)

    for kind, finder in order:
        path = finder()
        if path:
            return (kind, path)
    return None
