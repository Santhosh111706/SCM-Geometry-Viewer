"""
parser/scm_parser.py
--------------------
Turns a Sentaurus SDE .scm file into a ParseResult (regions, contacts,
refinement windows, parameters, log messages).

The heavy lifting is done by SchemeEvaluator.  This module only supplies
the domain hooks: what should happen when the script calls
sdegeo:create-cuboid, sdegeo:define-contact-set, and so on.

Design note
-----------
Rather than pattern-matching text, we actually *execute* the script.  That
means helper procedures, let bindings and nested arithmetic all work with
no extra effort, and the numbers we get are exactly the numbers SDE would
compute.  The cost is that we must tolerate commands we do not model:
every unrecognised call is logged and evaluates to None.
"""

import re
from typing import Dict, List, Optional

from models.region import (Region, Contact, RefinementWindow, Message,
                           ParseResult)
from parser.expression_evaluator import (SchemeEvaluator, read_forms,
                                         ReaderError, Symbol)


# ---------------------------------------------------------------------------
#  Small value objects returned by geometry helper calls
# ---------------------------------------------------------------------------
class Position(tuple):
    """Result of (position x y z)."""
    def __new__(cls, x, y, z):
        return super().__new__(cls, (float(x), float(y), float(z)))


class FaceRef:
    """Result of (find-face-id (position ...)) - we only keep the point."""
    __slots__ = ("point",)

    def __init__(self, point):
        self.point = point


class RGB(tuple):
    def __new__(cls, r, g, b):
        return super().__new__(cls, (float(r), float(g), float(b)))


# ---------------------------------------------------------------------------
#  Commands we knowingly ignore.  Listing them keeps the log clean: these
#  are real SDE commands that simply have no effect on the geometry we draw.
# ---------------------------------------------------------------------------
SILENTLY_IGNORED = {
    "sde:clear", "sde:set-process-up-direction", "sdegeo:set-default-boolean",
    "sde:build-mesh", "sde:save-model", "sde:setrefprop",
    "sdedr:define-constant-profile", "sdedr:define-gaussian-profile",
    "sdedr:define-erf-profile", "sdedr:define-analytical-profile",
    "sdedr:define-refinement-size", "sdedr:define-refinement-placement",
    "sdedr:define-refinement-function", "sdedr:define-refinement-region",
    "sdedr:define-constant-profile-placement",
    "sdedr:define-constant-profile-region",
    "sdedr:define-constant-profile-material",
    "sdedr:define-analytical-profile-placement",
    "sdedr:define-profile-placement",
    "sdegeo:set-current-contact-set",
    "sdeio:save-tdr-bnd", "sdeio:load-tdr-bnd",
    "sde:add-material", "sdegeo:delete-region",
}


class ScmParser:
    """Parse SDE Scheme into geometry objects."""

    def __init__(self):
        self.result = ParseResult()

    # -- logging ------------------------------------------------------------
    def _log(self, level: str, text: str, line: Optional[int] = None):
        self.result.messages.append(Message(level, text, line))

    # -- public -------------------------------------------------------------
    def parse_file(self, path: str, overrides: Dict[str, float] = None) -> ParseResult:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return self.parse_text(fh.read(), overrides)

    def parse_text(self, text: str,
                   overrides: Dict[str, float] = None) -> ParseResult:
        self.result = ParseResult(source_text=text)

        # ---- read ---------------------------------------------------------
        try:
            forms = read_forms(text)
        except ReaderError as exc:
            self._log("error", f"could not read the file: {exc}",
                      getattr(exc, "line", None))
            return self.result
        except Exception as exc:                              # noqa: BLE001
            self._log("error", f"tokenizer failure: {exc}")
            return self.result

        self._log("info", f"read {len(forms)} top-level forms")

        # ---- evaluate -----------------------------------------------------
        ev = SchemeEvaluator(hooks=self._hooks(),
                             on_unknown=self._unknown,
                             on_error=lambda m, l: self._log("error", m, l))
        try:
            env = ev.run(forms, overrides=overrides)
        except RecursionError as exc:
            self._log("error", f"aborted: {exc}")
            return self.result

        # ---- collect scalar parameters -------------------------------------
        # Anything defined at top level that ended up as a plain number is a
        # candidate parameter for the editor.
        for k, v in env.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                self.result.all_defines[k] = v
        self.result.parameters = self._pick_editable(text, self.result.all_defines)

        # ---- post-process ---------------------------------------------------
        for c in self.result.contacts:
            c.resolve(self.result.regions)

        self._sanity_pass()

        self._log("info", self.result.summary())
        return self.result

    # -- hook table ---------------------------------------------------------
    def _hooks(self):
        def h(fn):
            """Wrap so every hook accepts and ignores the _line kwarg."""
            return fn

        return {
            "position": h(self._position),
            "sdegeo:create-cuboid": h(self._create_cuboid),
            "sdegeo:define-contact-set": h(self._define_contact_set),
            "sdegeo:set-contact-faces": h(self._set_contact_faces),
            "sdegeo:set-current-contact-set": h(self._set_current_contact),
            "find-face-id": h(self._find_face_id),
            "find-body-id": h(self._find_face_id),
            "color:rgb": h(self._color_rgb),
            "sdedr:define-refinement-window": h(self._refinement_window),
        }

    # -- individual hooks ---------------------------------------------------
    def _position(self, x, y, z, _line=None):
        try:
            return Position(x, y, z)
        except (TypeError, ValueError):
            self._log("error",
                      f"malformed (position {x} {y} {z}): coordinates must be "
                      f"numbers", _line)
            return None

    def _color_rgb(self, r, g, b, _line=None):
        try:
            return RGB(r, g, b)
        except (TypeError, ValueError):
            return None

    def _find_face_id(self, point, _line=None):
        if isinstance(point, Position):
            return FaceRef(point)
        self._log("warning", "find-face-id called without a (position ...)",
                  _line)
        return None

    def _create_cuboid(self, p0, p1, material=None, name=None, _line=None):
        # validate the corners
        if not isinstance(p0, Position) or not isinstance(p1, Position):
            self._log("error",
                      f"create-cuboid for region '{name}': corner is not a "
                      f"valid (position x y z)", _line)
            return None
        if material is None or not isinstance(material, str):
            self._log("error",
                      f"create-cuboid at line {_line}: missing material name",
                      _line)
            return None
        if name is None:
            name = f"region_{len(self.result.regions) + 1}"
            self._log("warning",
                      f"create-cuboid with no region name; called it '{name}'",
                      _line)

        reg = Region(name=str(name), material=str(material),
                     x0=p0[0], y0=p0[1], z0=p0[2],
                     x1=p1[0], y1=p1[1], z1=p1[2],
                     source_line=_line)

        if reg.swapped:
            self._log("warning",
                      f"region '{name}': corners were given in reverse order, "
                      f"normalised", _line)
        if reg.is_degenerate:
            self._log("error",
                      f"region '{name}' has a zero or negative dimension "
                      f"({reg.lx:g} x {reg.ly:g} x {reg.lz:g}); it will not be "
                      f"drawn", _line)
            return None

        dup = self.result.region_by_name(reg.name)
        if dup is not None:
            self._log("warning",
                      f"duplicate region name '{reg.name}' (first seen at line "
                      f"{dup.source_line}); both are kept", _line)

        self.result.regions.append(reg)
        return reg

    def _define_contact_set(self, name, *rest, _line=None):
        color = None
        for r in rest:
            if isinstance(r, RGB):
                color = tuple(r)
        c = Contact(name=str(name), color=color, source_line=_line)
        self.result.contacts.append(c)
        return c

    def _set_current_contact(self, name, _line=None):
        self._current_contact = str(name)
        return None

    def _set_contact_faces(self, faces, name=None, _line=None):
        target = str(name) if name is not None else getattr(
            self, "_current_contact", None)
        if target is None:
            self._log("warning",
                      "set-contact-faces with no contact name and no current "
                      "contact set", _line)
            return None

        # faces may be a single FaceRef or a (list ...) of them
        if isinstance(faces, FaceRef):
            faces = [faces]
        if not isinstance(faces, list):
            faces = [faces]

        pts = [f.point for f in faces if isinstance(f, FaceRef)]
        if not pts:
            self._log("warning",
                      f"contact '{target}': no usable face pick point found",
                      _line)
            return None

        for c in self.result.contacts:
            if c.name == target:
                c.position = pts[0]
                if c.source_line is None:
                    c.source_line = _line
                return None

        # contact used without being defined first
        self.result.contacts.append(
            Contact(name=target, position=pts[0], source_line=_line))
        self._log("warning",
                  f"contact '{target}' had faces set before it was defined",
                  _line)
        return None

    def _refinement_window(self, name, kind, p0=None, p1=None, _line=None):
        if not (isinstance(p0, Position) and isinstance(p1, Position)):
            self._log("warning",
                      f"refinement window '{name}': unsupported form "
                      f"'{kind}', skipped", _line)
            return None
        self.result.windows.append(
            RefinementWindow(str(name), p0[0], p0[1], p0[2],
                             p1[0], p1[1], p1[2]))
        return None

    # -- unknown command reporting -----------------------------------------
    def _unknown(self, name: str, line: Optional[int]):
        if name in SILENTLY_IGNORED:
            return
        self._log("warning",
                  f"unsupported command '{name}' was ignored; geometry "
                  f"parsing continued", line)

    # -- parameter discovery ------------------------------------------------
    @staticmethod
    def _pick_editable(text: str, all_defines: Dict[str, float]) -> Dict[str, float]:
        """
        A parameter is a top-level (define NAME <literal-number>).

        Defines whose value is an expression are *derived*, so editing them
        directly would be meaningless: they get recomputed from the literals
        on the next pass.  We find the literals by scanning the source.
        """
        editable = {}
        pattern = re.compile(
            r"^\s*\(define\s+([A-Za-z_][\w:!?*/+\-<>=]*)\s+"
            r"(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\)",
            re.MULTILINE)
        for m in pattern.finditer(text):
            nm = m.group(1)
            if nm in all_defines:
                editable[nm] = float(m.group(2))
        return editable

    # -- light geometric sanity, reported as warnings -----------------------
    def _sanity_pass(self):
        regs = self.result.regions
        if not regs:
            self._log("warning",
                      "no cuboid regions were created; check that the file "
                      "uses sdegeo:create-cuboid")
            return
        tiny = [r for r in regs if r.min_dimension < 1e-6]
        for r in tiny[:5]:
            self._log("warning",
                      f"region '{r.name}' is very thin "
                      f"({r.min_dimension:g}); possible sliver",
                      r.source_line)
        missing = [c.name for c in self.result.contacts if c.position is None]
        if missing:
            self._log("warning",
                      f"{len(missing)} contact set(s) were declared but never "
                      f"attached to a face: {', '.join(missing)}")


# ---------------------------------------------------------------------------
#  Writing a modified SCM back out
# ---------------------------------------------------------------------------
def write_modified_scm(original_text: str, new_params: Dict[str, float]) -> str:
    """
    Return the original file with the given top-level scalar defines
    replaced.  Comments, helper procedures and everything else are left
    untouched, so the file stays readable and re-parsable.
    """
    out = original_text
    for name, value in new_params.items():
        pat = re.compile(
            r"(^\s*\(define\s+" + re.escape(name) + r"\s+)"
            r"(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(\s*\))",
            re.MULTILINE)

        def repl(m, v=value):
            return f"{m.group(1)}{v:g}{m.group(3)}"

        out, n = pat.subn(repl, out)
        if n == 0:
            # parameter did not exist as a literal define; append it near the
            # top so the file stays valid rather than silently losing the edit
            out = f"(define {name} {value:g})\n" + out
    return out