"""
validation/geometry_checks.py
-----------------------------
Optional structural checks for a parsed device.

These are geometric only.  They do not know anything about physics, and
they never modify the model: each check returns a verdict plus a short
explanation that the GUI shows in the validation panel.

The Forksheet-specific checks look for the naming convention used by the
V7 structure ("n_" / "p_" prefixes, "_chan", "_extS", "Sheet", "gate",
"ForkWall").  When a file does not follow that convention, the check
reports SKIPPED rather than failing, so a plain MOSFET file does not fill
the panel with red.
"""

import itertools
from dataclasses import dataclass
from typing import List, Optional

from models.region import Region, Contact

EPS = 1e-12

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    items: Optional[List[str]] = None

    def __str__(self):
        return f"[{self.status}] {self.name}: {self.detail}"


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _contact_faces(a: Region, b: Region) -> bool:
    """Share a face or overlap: i.e. are they in physical contact?"""
    gaps = (min(a.x1, b.x1) - max(a.x0, b.x0),
            min(a.y1, b.y1) - max(a.y0, b.y0),
            min(a.z1, b.z1) - max(a.z0, b.z0))
    return all(g > -EPS for g in gaps) and sum(1 for g in gaps if g > EPS) >= 2


def _devices(regions: List[Region]):
    """Split regions into nMOS / pMOS by name prefix, if the file uses them."""
    n = [r for r in regions if r.name.startswith("n_")]
    p = [r for r in regions if r.name.startswith("p_")]
    return n, p


# ---------------------------------------------------------------------------
#  individual checks
# ---------------------------------------------------------------------------
def check_dimensions(regions) -> CheckResult:
    bad = [r.name for r in regions if r.is_degenerate]
    if bad:
        return CheckResult("Valid cuboid dimensions", FAIL,
                           f"{len(bad)} region(s) have a zero or negative "
                           f"dimension", bad[:20])
    thin = [f"{r.name} ({r.min_dimension:g})"
            for r in regions if r.min_dimension < 1e-4]
    if thin:
        return CheckResult("Valid cuboid dimensions", WARN,
                           f"all {len(regions)} regions are positive, but "
                           f"{len(thin)} are very thin", thin[:20])
    return CheckResult("Valid cuboid dimensions", PASS,
                       f"all {len(regions)} regions have positive extents")


def check_sheet_thickness(regions) -> CheckResult:
    sheets = [r for r in regions if "Sheet" in r.name or "sheet" in r.name]
    if not sheets:
        return CheckResult("Nanosheet thickness > 0", SKIP,
                           "no regions with 'Sheet' in the name")
    bad = [r.name for r in sheets if r.ly <= EPS]
    if bad:
        return CheckResult("Nanosheet thickness > 0", FAIL,
                           "sheet with non-positive Y extent", bad)
    vals = sorted({round(r.ly, 9) for r in sheets})
    return CheckResult("Nanosheet thickness > 0", PASS,
                       f"{len(sheets)} sheet regions, thickness "
                       f"{', '.join(f'{v:g}' for v in vals)}")


def check_sheet_width(regions) -> CheckResult:
    sheets = [r for r in regions if "Sheet" in r.name or "sheet" in r.name]
    if not sheets:
        return CheckResult("Nanosheet width > 0", SKIP,
                           "no regions with 'Sheet' in the name")
    bad = [r.name for r in sheets if r.lz <= EPS]
    if bad:
        return CheckResult("Nanosheet width > 0", FAIL,
                           "sheet with non-positive Z extent", bad)
    vals = sorted({round(r.lz, 9) for r in sheets})
    return CheckResult("Nanosheet width > 0", PASS,
                       f"{len(sheets)} sheet regions, width "
                       f"{', '.join(f'{v:g}' for v in vals)}")


def check_fork_wall(regions) -> CheckResult:
    walls = [r for r in regions
             if "fork" in r.name.lower() or "wall" in r.name.lower()]
    if not walls:
        return CheckResult("Fork wall thickness > 0", SKIP,
                           "no region with 'fork' or 'wall' in the name")
    w = walls[0]
    thick = min(w.lx, w.ly, w.lz)
    if thick <= EPS:
        return CheckResult("Fork wall thickness > 0", FAIL,
                           f"'{w.name}' has zero thickness")
    return CheckResult("Fork wall thickness > 0", PASS,
                       f"'{w.name}' is {thick:g} thick ({w.material})")


def check_overlaps(regions) -> CheckResult:
    ov = [(a.name, b.name)
          for a, b in itertools.combinations(regions, 2) if a.overlaps(b)]
    n_pairs = len(regions) * (len(regions) - 1) // 2
    if ov:
        return CheckResult("No overlapping volumes", FAIL,
                           f"{len(ov)} overlapping pair(s) out of {n_pairs} "
                           f"tested",
                           [f"{a} <-> {b}" for a, b in ov[:20]])
    return CheckResult("No overlapping volumes", PASS,
                       f"{n_pairs} region pairs tested, none share volume")


def check_gate_dielectric(regions) -> CheckResult:
    """Gate metal must never touch semiconductor directly."""
    metals = [r for r in regions if r.material in ("TiN", "Aluminum", "Copper",
                                                   "PolySilicon")]
    semis = [r for r in regions if r.material in ("Silicon", "Germanium")]
    if not metals or not semis:
        return CheckResult("Gate dielectric separates metal and channel", SKIP,
                           "no metal or no semiconductor regions found")
    bad = [f"{m.name} <-> {s.name}"
           for m in metals for s in semis if _contact_faces(m, s)]
    if bad:
        return CheckResult("Gate dielectric separates metal and channel", FAIL,
                           f"{len(bad)} direct metal-to-semiconductor "
                           f"contact(s)", bad[:20])
    return CheckResult("Gate dielectric separates metal and channel", PASS,
                       f"{len(metals)} metal and {len(semis)} semiconductor "
                       f"regions, no direct contact")


def check_gate_connectivity(regions) -> CheckResult:
    """Every gate metal body of one device must form a single connected set."""
    n, p = _devices(regions)
    groups = []
    if n or p:
        for tag, group in (("nMOS", n), ("pMOS", p)):
            metals = [r for r in group if r.material == "TiN"]
            if metals:
                groups.append((tag, metals))
    else:
        metals = [r for r in regions if r.material == "TiN"]
        if metals:
            groups.append(("gate", metals))

    if not groups:
        return CheckResult("Gate metal is one connected body", SKIP,
                           "no TiN regions found")

    details, failed = [], False
    for tag, metals in groups:
        adj = {m.name: set() for m in metals}
        for a, b in itertools.combinations(metals, 2):
            if a.touches(b) or a.overlaps(b):
                adj[a.name].add(b.name)
                adj[b.name].add(a.name)
        seen, stack = {metals[0].name}, [metals[0].name]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(seen) == len(metals):
            details.append(f"{tag}: {len(metals)}/{len(metals)} connected")
        else:
            failed = True
            floating = sorted(set(adj) - seen)
            details.append(f"{tag}: only {len(seen)}/{len(metals)} connected, "
                           f"floating: {', '.join(floating)}")
    return CheckResult("Gate metal is one connected body",
                       FAIL if failed else PASS,
                       "; ".join(details))


def check_source_channel_drain(regions) -> CheckResult:
    """
    Silicon must form an unbroken chain along X for every sheet.

    Works on the V7 naming: <tag>_Source, <tag>_Sheet<k>_extS/_chan/_extD,
    <tag>_Drain.
    """
    chains, problems = 0, []
    for tag in ("n", "p"):
        src = next((r for r in regions if r.name == f"{tag}_Source"), None)
        drn = next((r for r in regions if r.name == f"{tag}_Drain"), None)
        if src is None or drn is None:
            continue
        for k in ("1", "2", "3", "4", "5"):
            segs = [r for r in regions
                    if r.name.startswith(f"{tag}_Sheet{k}_")]
            if not segs:
                continue
            segs.sort(key=lambda r: r.x0)
            path = [src] + segs + [drn]
            broken = []
            for a, b in zip(path, path[1:]):
                if abs(a.x1 - b.x0) > 1e-9:
                    broken.append(f"gap between {a.name} and {b.name}")
                elif not (b.y0 >= a.y0 - 1e-9 and b.y1 <= a.y1 + 1e-9 and
                          b.z0 >= a.z0 - 1e-9 and b.z1 <= a.z1 + 1e-9) and \
                        not (a.y0 >= b.y0 - 1e-9 and a.y1 <= b.y1 + 1e-9 and
                             a.z0 >= b.z0 - 1e-9 and a.z1 <= b.z1 + 1e-9):
                    broken.append(f"{a.name} and {b.name} touch but their "
                                  f"cross-sections do not nest")
            chains += 1
            if broken:
                problems.extend(broken)

    if chains == 0:
        return CheckResult("Source - channel - drain continuity", SKIP,
                           "no <tag>_Source / _Sheet / _Drain naming found")
    if problems:
        return CheckResult("Source - channel - drain continuity", FAIL,
                           f"{len(problems)} break(s) across {chains} sheet "
                           f"chain(s)", problems[:20])
    return CheckResult("Source - channel - drain continuity", PASS,
                       f"{chains} sheet chain(s) continuous from source to "
                       f"drain")


def check_nmos_pmos_isolation(regions) -> CheckResult:
    n, p = _devices(regions)
    if not n or not p:
        return CheckResult("nMOS / pMOS isolated by the fork wall", SKIP,
                           "file does not use n_ / p_ region prefixes")
    bad = []
    for mat in ("TiN", "Silicon"):
        ns = [r for r in n if r.material == mat]
        ps = [r for r in p if r.material == mat]
        for a in ns:
            for b in ps:
                if _contact_faces(a, b):
                    bad.append(f"{mat}: {a.name} <-> {b.name}")
    if bad:
        return CheckResult("nMOS / pMOS isolated by the fork wall", FAIL,
                           f"{len(bad)} contact(s) across the wall", bad[:20])
    return CheckResult("nMOS / pMOS isolated by the fork wall", PASS,
                       f"{len(n)} nMOS and {len(p)} pMOS regions, no silicon "
                       f"or metal contact between them")


def check_material_conflicts(regions) -> CheckResult:
    """Overlap between incompatible materials is worse than overlap in general."""
    bad = []
    for a, b in itertools.combinations(regions, 2):
        if a.material != b.material and a.overlaps(b):
            bad.append(f"{a.name} ({a.material}) <-> {b.name} ({b.material})")
    if bad:
        return CheckResult("No incompatible material overlap", FAIL,
                           f"{len(bad)} overlapping pair(s) of differing "
                           f"material", bad[:20])
    return CheckResult("No incompatible material overlap", PASS,
                       "no two regions of different material share volume")


def check_contacts(regions, contacts: List[Contact]) -> CheckResult:
    if not contacts:
        return CheckResult("Contacts attached to real faces", SKIP,
                           "no contacts found in the file")
    unattached = [c.name for c in contacts if c.position is None]
    unresolved = [c.name for c in contacts
                  if c.position is not None and c.region_name is None]
    interior = [f"{c.name} (inside {c.region_name})"
                for c in contacts if c.face == "interior"]
    problems = []
    if unattached:
        problems.append(f"declared but never attached: {', '.join(unattached)}")
    if unresolved:
        problems.append(f"pick point is not inside any region: "
                        f"{', '.join(unresolved)}")
    if interior:
        problems.append(f"pick point is inside a volume, not on a face: "
                        f"{', '.join(interior)}")
    if problems:
        return CheckResult("Contacts attached to real faces", FAIL,
                           f"{len(contacts)} contact(s) checked", problems)
    return CheckResult("Contacts attached to real faces", PASS,
                       f"all {len(contacts)} contacts sit on an external face")


# ---------------------------------------------------------------------------
#  driver
# ---------------------------------------------------------------------------
def run_all_checks(regions: List[Region],
                   contacts: List[Contact] = None) -> List[CheckResult]:
    contacts = contacts or []
    if not regions:
        return [CheckResult("Geometry present", FAIL, "no regions were parsed")]
    return [
        check_dimensions(regions),
        check_sheet_thickness(regions),
        check_sheet_width(regions),
        check_fork_wall(regions),
        check_overlaps(regions),
        check_material_conflicts(regions),
        check_gate_dielectric(regions),
        check_gate_connectivity(regions),
        check_source_channel_drain(regions),
        check_nmos_pmos_isolation(regions),
        check_contacts(regions, contacts),
    ]