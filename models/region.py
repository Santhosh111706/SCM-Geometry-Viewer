"""
models/region.py
-----------------
Plain data containers for everything the parser extracts from an SCM file.

Nothing in here imports Qt or VTK, so these objects can be used headlessly
(unit tests, batch validation, command-line tools).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict


# ---------------------------------------------------------------------------
#  Material colour table
#
#  Colours follow the scheme requested for the viewer.  Any material not
#  listed here falls back to DEFAULT_COLOR and a warning is logged once.
# ---------------------------------------------------------------------------
MATERIAL_COLORS: Dict[str, Tuple[float, float, float]] = {
    "Silicon":   (0.25, 0.45, 0.85),   # blue
    "HfO2":      (0.95, 0.65, 0.15),   # yellow / orange
    "SiO2":      (0.60, 0.90, 0.95),   # light cyan
    "Si3N4":     (0.30, 0.75, 0.35),   # green
    "TiN":       (0.55, 0.57, 0.60),   # grey
    "PolySilicon": (0.80, 0.40, 0.70),
    "Aluminum":  (0.75, 0.75, 0.78),
    "Copper":    (0.85, 0.55, 0.35),
    "Germanium": (0.40, 0.35, 0.70),
    "Air":       (0.90, 0.90, 0.90),
    "Gas":       (0.90, 0.90, 0.90),
}

DEFAULT_COLOR: Tuple[float, float, float] = (0.70, 0.70, 0.70)

# Regions whose *name* matches one of these prefixes get a darker shade of
# their material colour, so the substrate reads differently from the active
# silicon even though both are "Silicon".
DARK_NAME_PREFIXES = ("substrate", "sub_", "bulk", "handle")
DARK_FACTOR = 0.55


def material_color(material: str, region_name: str = "") -> Tuple[float, float, float]:
    """Colour for a region, with a darker variant for substrate-like names."""
    base = MATERIAL_COLORS.get(material, DEFAULT_COLOR)
    low = region_name.lower()
    if any(low.startswith(p) or ("_" + p) in low for p in DARK_NAME_PREFIXES):
        return tuple(c * DARK_FACTOR for c in base)
    return base


@dataclass
class Region:
    """One axis-aligned cuboid region created by sdegeo:create-cuboid."""

    name: str
    material: str
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    source_line: Optional[int] = None

    # -- normalisation ------------------------------------------------------
    def __post_init__(self):
        # SDE accepts the two corners in either order; normalise so that
        # x0 < x1 etc.  We remember whether we had to swap, because a swap
        # is worth reporting: it usually means the SCM had them backwards.
        self.swapped = False
        if self.x0 > self.x1:
            self.x0, self.x1 = self.x1, self.x0
            self.swapped = True
        if self.y0 > self.y1:
            self.y0, self.y1 = self.y1, self.y0
            self.swapped = True
        if self.z0 > self.z1:
            self.z0, self.z1 = self.z1, self.z0
            self.swapped = True

    # -- derived quantities -------------------------------------------------
    @property
    def lx(self) -> float:
        return self.x1 - self.x0

    @property
    def ly(self) -> float:
        return self.y1 - self.y0

    @property
    def lz(self) -> float:
        return self.z1 - self.z0

    @property
    def volume(self) -> float:
        return self.lx * self.ly * self.lz

    @property
    def center(self) -> Tuple[float, float, float]:
        return ((self.x0 + self.x1) / 2,
                (self.y0 + self.y1) / 2,
                (self.z0 + self.z1) / 2)

    @property
    def bounds(self) -> Tuple[float, float, float, float, float, float]:
        """VTK-style bounds tuple."""
        return (self.x0, self.x1, self.y0, self.y1, self.z0, self.z1)

    @property
    def is_degenerate(self) -> bool:
        """True if any dimension is zero or negative."""
        eps = 1e-12
        return self.lx <= eps or self.ly <= eps or self.lz <= eps

    @property
    def min_dimension(self) -> float:
        return min(self.lx, self.ly, self.lz)

    def color(self) -> Tuple[float, float, float]:
        return material_color(self.material, self.name)

    def contains_point(self, p, eps: float = 1e-12) -> bool:
        x, y, z = p
        return (self.x0 - eps <= x <= self.x1 + eps and
                self.y0 - eps <= y <= self.y1 + eps and
                self.z0 - eps <= z <= self.z1 + eps)

    def overlaps(self, other: "Region", eps: float = 1e-12) -> bool:
        """True only for a real shared VOLUME.  Touching faces are not an
        overlap: adjacent regions are normal and required."""
        return (min(self.x1, other.x1) - max(self.x0, other.x0) > eps and
                min(self.y1, other.y1) - max(self.y0, other.y0) > eps and
                min(self.z1, other.z1) - max(self.z0, other.z0) > eps)

    def touches(self, other: "Region", eps: float = 1e-12) -> bool:
        """True if the two regions share a 2D face (contact, no overlap)."""
        gaps = (min(self.x1, other.x1) - max(self.x0, other.x0),
                min(self.y1, other.y1) - max(self.y0, other.y0),
                min(self.z1, other.z1) - max(self.z0, other.z0))
        return (all(g > -eps for g in gaps) and
                sum(1 for g in gaps if abs(g) < eps) == 1 and
                sum(1 for g in gaps if g > eps) == 2)

    def __str__(self):
        return f"{self.name} [{self.material}]"


@dataclass
class Contact:
    """A contact set plus the pick point used to attach it to a face."""

    name: str
    position: Optional[Tuple[float, float, float]] = None
    color: Optional[Tuple[float, float, float]] = None
    region_name: Optional[str] = None     # resolved after regions are known
    face: Optional[str] = None            # "+X", "-Y", ... resolved likewise
    source_line: Optional[int] = None

    def resolve(self, regions: List[Region], eps: float = 1e-9):
        """Work out which region the pick point sits on and which face."""
        if self.position is None:
            return
        px, py, pz = self.position
        for r in regions:
            if not r.contains_point(self.position, eps):
                continue
            # Which face of this region is the point on?
            for axis, lo, hi, neg, pos in (
                (px, r.x0, r.x1, "-X", "+X"),
                (py, r.y0, r.y1, "-Y", "+Y"),
                (pz, r.z0, r.z1, "-Z", "+Z"),
            ):
                if abs(axis - lo) < eps:
                    self.region_name, self.face = r.name, neg
                    return
                if abs(axis - hi) < eps:
                    self.region_name, self.face = r.name, pos
                    return
            # inside the region but not on a face
            self.region_name, self.face = r.name, "interior"
            return


@dataclass
class RefinementWindow:
    """A sdedr:define-refinement-window cuboid, drawn as a wireframe box."""
    name: str
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float

    @property
    def bounds(self):
        return (min(self.x0, self.x1), max(self.x0, self.x1),
                min(self.y0, self.y1), max(self.y0, self.y1),
                min(self.z0, self.z1), max(self.z0, self.z1))


@dataclass
class Message:
    """One entry for the parsing log panel."""
    level: str            # "info" | "warning" | "error"
    text: str
    line: Optional[int] = None

    def __str__(self):
        loc = f" (line {self.line})" if self.line else ""
        return f"[{self.level.upper()}]{loc} {self.text}"


@dataclass
class ParseResult:
    """Everything one parse pass produced."""
    regions: List[Region] = field(default_factory=list)
    contacts: List[Contact] = field(default_factory=list)
    windows: List[RefinementWindow] = field(default_factory=list)
    parameters: Dict[str, float] = field(default_factory=dict)
    all_defines: Dict[str, object] = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)
    source_text: str = ""

    # -- convenience --------------------------------------------------------
    @property
    def materials(self) -> List[str]:
        seen = []
        for r in self.regions:
            if r.material not in seen:
                seen.append(r.material)
        return sorted(seen)

    @property
    def errors(self) -> List[Message]:
        return [m for m in self.messages if m.level == "error"]

    @property
    def warnings(self) -> List[Message]:
        return [m for m in self.messages if m.level == "warning"]

    def bounds(self):
        """Overall bounding box of all regions, or None if empty."""
        if not self.regions:
            return None
        return (min(r.x0 for r in self.regions), max(r.x1 for r in self.regions),
                min(r.y0 for r in self.regions), max(r.y1 for r in self.regions),
                min(r.z0 for r in self.regions), max(r.z1 for r in self.regions))

    def region_by_name(self, name: str) -> Optional[Region]:
        for r in self.regions:
            if r.name == name:
                return r
        return None

    def summary(self) -> str:
        b = self.bounds()
        if b is None:
            return "0 regions"
        return (f"{len(self.regions)} regions, {len(self.materials)} materials, "
                f"{len(self.contacts)} contacts | bbox "
                f"X {b[0]:.4g}..{b[1]:.4g}  "
                f"Y {b[2]:.4g}..{b[3]:.4g}  "
                f"Z {b[4]:.4g}..{b[5]:.4g}")