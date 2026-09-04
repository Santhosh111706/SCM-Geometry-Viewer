"""
viewer/vtk_viewer.py
--------------------
The 3D view.  Wraps pyvistaqt.QtInteractor so the rest of the GUI does not
have to know about VTK.

Responsibilities:
    - build one actor per Region
    - material colours, per-material and per-region visibility
    - global transparency, surface / wireframe style
    - camera presets and orthographic / perspective toggle
    - contact markers with labels
    - click-to-select, reporting the picked region back to the GUI
"""

from typing import Dict, List, Optional, Callable

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor

from models.region import Region, Contact, RefinementWindow


CONTACT_COLOR = "#ff2d55"
WINDOW_COLOR = "#888888"


class DeviceViewer(QtInteractor):
    """Interactive 3D device view."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.regions: List[Region] = []
        self.contacts: List[Contact] = []
        self.windows: List[RefinementWindow] = []

        self._region_actors: Dict[str, object] = {}
        self._contact_actors: Dict[str, list] = {}
        self._window_actors: List[object] = []

        self._hidden_regions = set()
        self._hidden_materials = set()
        self._opacity = 1.0
        self._style = "surface"          # or "wireframe"
        self._show_edges = True
        self._show_contacts = True
        self._show_windows = False

        self.on_pick: Optional[Callable[[Optional[str]], None]] = None

        self.set_background("#1b1f24", top="#2b323b")
        self.add_axes(interactive=False)
        self.enable_trackball_style()

    # ==================================================================
    #  building
    # ==================================================================
    def load(self, regions: List[Region], contacts: List[Contact] = None,
             windows: List[RefinementWindow] = None, reset_camera: bool = True):
        """Replace the whole scene."""
        self.regions = regions or []
        self.contacts = contacts or []
        self.windows = windows or []
        self._rebuild(reset_camera=reset_camera)

    def _rebuild(self, reset_camera: bool = False):
        cam = None if reset_camera else self.camera_position
        self.clear()
        self._region_actors.clear()
        self._contact_actors.clear()
        self._window_actors.clear()

        for r in self.regions:
            if r.name in self._hidden_regions or r.material in self._hidden_materials:
                continue
            self._add_region(r)

        if self._show_contacts:
            self._add_contacts()
        if self._show_windows:
            self._add_windows()

        self.add_axes(interactive=False)

        if reset_camera or cam is None:
            self.reset_camera()
            self.view_isometric()
        else:
            self.camera_position = cam
        self.render()

    def _add_region(self, r: Region):
        box = pv.Box(bounds=r.bounds)
        actor = self.add_mesh(
            box,
            color=r.color(),
            opacity=self._opacity,
            style=self._style,
            show_edges=self._show_edges and self._style == "surface",
            edge_color="#11151a",
            line_width=1,
            name=f"region::{r.name}",
            pickable=True,
            smooth_shading=False,
        )
        self._region_actors[r.name] = actor

    def _add_contacts(self):
        if not self.regions:
            return
        b = self._bbox()
        scale = max(b[1] - b[0], b[3] - b[2], b[5] - b[4]) * 0.02

        pts, labels = [], []
        for c in self.contacts:
            if c.position is None:
                continue
            pts.append(list(c.position))
            labels.append(c.name)
            sphere = pv.Sphere(radius=scale, center=c.position)
            a = self.add_mesh(sphere, color=CONTACT_COLOR, opacity=1.0,
                              name=f"contact::{c.name}", pickable=False)
            self._contact_actors.setdefault(c.name, []).append(a)

        if pts:
            lab = self.add_point_labels(
                np.array(pts), labels,
                font_size=12, text_color="white", shape_color="#c0392b",
                shape_opacity=0.75, point_size=1, always_visible=True,
                name="contact_labels", pickable=False)
            self._contact_actors.setdefault("__labels__", []).append(lab)

    def _add_windows(self):
        for w in self.windows:
            box = pv.Box(bounds=w.bounds)
            a = self.add_mesh(box, color=WINDOW_COLOR, style="wireframe",
                              line_width=1, opacity=0.6,
                              name=f"window::{w.name}", pickable=False)
            self._window_actors.append(a)

    def _bbox(self):
        if not self.regions:
            return (0, 1, 0, 1, 0, 1)
        return (min(r.x0 for r in self.regions), max(r.x1 for r in self.regions),
                min(r.y0 for r in self.regions), max(r.y1 for r in self.regions),
                min(r.z0 for r in self.regions), max(r.z1 for r in self.regions))

    # ==================================================================
    #  display options
    # ==================================================================
    def set_material_visible(self, material: str, visible: bool):
        if visible:
            self._hidden_materials.discard(material)
        else:
            self._hidden_materials.add(material)
        self._rebuild()

    def set_region_visible(self, name: str, visible: bool):
        if visible:
            self._hidden_regions.discard(name)
        else:
            self._hidden_regions.add(name)
        self._rebuild()

    def hide_regions(self, names):
        self._hidden_regions.update(names)
        self._rebuild()

    def show_all(self):
        self._hidden_regions.clear()
        self._hidden_materials.clear()
        self._rebuild()

    def set_opacity(self, value: float):
        self._opacity = max(0.05, min(1.0, float(value)))
        self._rebuild()

    def set_style(self, style: str):
        self._style = "wireframe" if style == "wireframe" else "surface"
        self._rebuild()

    def set_show_edges(self, on: bool):
        self._show_edges = bool(on)
        self._rebuild()

    def set_show_contacts(self, on: bool):
        self._show_contacts = bool(on)
        self._rebuild()

    def set_show_windows(self, on: bool):
        self._show_windows = bool(on)
        self._rebuild()

    def highlight(self, name: Optional[str]):
        """Outline one region without rebuilding the whole scene."""
        self.remove_actor("__highlight__", render=False)
        if name:
            r = next((x for x in self.regions if x.name == name), None)
            if r is not None:
                self.add_mesh(pv.Box(bounds=r.bounds), color="#ffe14d",
                              style="wireframe", line_width=4,
                              name="__highlight__", pickable=False)
        self.render()

    # ==================================================================
    #  camera
    # ==================================================================
    def set_parallel(self, on: bool):
        if on:
            self.enable_parallel_projection()
        else:
            self.disable_parallel_projection()
        self.render()

    def view_preset(self, which: str):
        which = which.lower()
        {
            "front": self.view_xy,
            "back": lambda: self.view_xy(negative=True),
            "left": self.view_yz,
            "right": lambda: self.view_yz(negative=True),
            "top": self.view_xz,
            "bottom": lambda: self.view_xz(negative=True),
        }.get(which, self.view_isometric)()
        self.reset_camera()
        self.render()

    def reset_view(self):
        self.reset_camera()
        self.view_isometric()
        self.render()

    # ==================================================================
    #  picking
    # ==================================================================
    def enable_region_picking(self):
        def _cb(mesh, *_):
            if mesh is None:
                if self.on_pick:
                    self.on_pick(None)
                return
            # match the picked mesh back to a region by its bounds
            b = mesh.bounds
            best, tol = None, 1e-9
            for r in self.regions:
                rb = r.bounds
                if all(abs(a - c) <= tol for a, c in zip(b, rb)):
                    best = r.name
                    break
            if self.on_pick:
                self.on_pick(best)

        try:
            self.enable_mesh_picking(callback=_cb, show=False,
                                     show_message=False, left_clicking=True)
        except TypeError:
            # older pyvista signature
            self.enable_mesh_picking(callback=_cb, show=False)

    # ==================================================================
    #  export
    # ==================================================================
    def save_screenshot_to(self, path: str):
        self.screenshot(path)