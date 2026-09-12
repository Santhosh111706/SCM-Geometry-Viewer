# SCM Geometry Viewer

**Live app:** https://santhosh111706.github.io/SCM-Geometry-Viewer/

A browser-based 3D viewer and analyser for **Synopsys Sentaurus Structure Editor
(SDE) Scheme (`.scm`) files**. Open a device script and inspect its regions,
materials, contacts, dimensions and parameters interactively — no install, no
server, no build step.

**Your files never leave your machine.** The `.scm` file is read with the
browser's `FileReader` API and parsed locally. There is no upload, no backend
and no telemetry of any kind.

---

## Why a real Scheme evaluator

Production SDE scripts do not just list geometry — they define helper procedures
and call them:

```scheme
(define (hfo2-collar tag st ya yb zha zca zcb zhb)
  (sdegeo:create-cuboid ...)
  (sdegeo:create-cuboid ...))

(hfo2-collar "n" "s1" ya1 yb1 znh0 znc0 znc1 znh1)
```

A regular-expression scanner sees one `create-cuboid` and misses the twelve the
procedure actually emits. This viewer ships a small **Scheme reader and
evaluator** that runs the file the way SDE would: `define`, `let`, `lambda`,
user procedures, arithmetic and conditionals are all evaluated, so dependent
coordinates resolve to their real values and procedure-generated geometry
appears in full.

---

## Features

### Viewing

- Cuboid regions drawn at their true coordinates, coloured by material
- Camera presets: **Front, Back, Left, Right, Top, Bottom, Isometric, Reset**
- **Perspective** and **Orthographic** projection
- **Surface** and **Wireframe** display modes
- Opacity slider (10–100 %)
- Toggles for **Edges**, **Contacts**, **Axes** and **Bounding box**
- Orbit controls: left-drag rotate · right-drag pan · scroll zoom
- Click any region in the viewport to select it; the picked region is outlined
  and named on screen
- Labelled X/Y/Z axes and a live view-scale readout (`1 world unit = N µm`)

### Model browsing

- **Parameters** panel — every top-level `(define NAME <number>)` is exposed as
  an editable field
- **Materials** panel — show or hide every region of a material at once
- **Regions** panel — full region list with a name filter, per-region
  visibility, *Hide selected* and *Show all*

### Inspection

- **Region tab** — name, material, X/Y/Z min and max, X/Y/Z lengths, total
  volume, *visible* volume after overlap resolution, fragment count, which
  region replaced it, and the source line it came from
- **Contacts tab** — contact list with its pick point, colour, resolved region
  and face
- **Model tab** — bounding box and sizes, aspect ratio, total volume, geometry
  statistics, an overall validity verdict, per-material breakdown, and quick
  structural checks

### Quick structural checks

| Check | Reported as |
|---|---|
| Sliver regions (smallest dimension < 1e-4) | warning |
| Duplicate region names | warning |
| Replacement overlaps / fully replaced regions | information |
| Regions with no volume that nothing replaced | failure |
| Contacts not attached to a region face | warning |

### Editing and export

- Edit any numeric parameter and press **Apply** (or <kbd>Enter</kbd>) to
  re-evaluate the whole file — every dependent coordinate recomputes and the
  model rebuilds
- **Reset** restores the values as written in the file
- **Save .scm** downloads `<filename>_modified.scm` with your edited `define`
  values written back into the original source text, leaving all other
  formatting untouched

### Parsing log

A bottom panel logs every step with error and warning counts. Problems are
reported **by source line** — unsupported commands, reversed cuboid corners,
degenerate dimensions, duplicate names and unresolved contacts. Parsing always
continues past a bad command rather than aborting.

### Layout

Three resizable columns plus a resizable log panel, a **Panels** button to
collapse the sidebars for a full-width viewport, and responsive breakpoints down
to phone width.

---

## Overlap handling

Overlapping regions are **legal** in SDE geometry: a later-created region
replaces the overlapping portion of earlier ones. The viewer reproduces this
faithfully — each earlier region is subtracted into fragments in SCM creation
order, so what you see is the *resolved* structure, not stacked boxes.

Overlap on its own is never treated as an error. It is reported as information,
and each region's *visible volume* and *fragment count* reflect the result.

To keep a pathological file from locking the browser, subtraction is capped at
512 fragments per region and 20 000 overall; past those limits the viewer draws
the original box and logs a warning instead of hanging. Evaluation recursion is
capped at depth 400.

---

## Supported SCM subset

| Command | Handling |
|---|---|
| `sdegeo:create-cuboid` | Full — creates a region |
| `sdegeo:define-contact-set` | Full — name and RGB colour |
| `sdegeo:set-current-contact-set` | Full |
| `sdegeo:set-contact-faces` | Full — resolves the pick point to a region face |
| `sdedr:define-refinement-window` | Rectangular windows recorded |
| `position`, `color:rgb`, `find-face-id`, `find-body-id` | Full |
| `define`, `let`, `lambda`, arithmetic, comparisons, `if` / `cond` | Evaluated |
| `sde:clear`, `sde:build-mesh`, `sde:save-model`, `sdedr:define-*-profile`, `sdedr:define-refinement-*`, `sdeio:*` | Accepted and ignored (no geometry effect) |
| Anything else | Ignored with a warning in the log; parsing continues |

Accepted file extensions: `.scm`, `.cmd`, `.txt`. Coordinates are interpreted in
micrometres (µm).

### Material colours

`Silicon`, `HfO2`, `SiO2`, `Si3N4`, `TiN`, `PolySilicon`, `Aluminum`, `Copper`,
`Germanium`, `Air`, `Gas`. Anything else falls back to neutral grey and is
reported once in the log.

Regions whose name starts with or contains `substrate`, `sub_`, `bulk` or
`handle` are shaded darker, so the bulk reads differently from active silicon
even though both are `Silicon`.

---

## Limitations

- **Boolean operations are not evaluated.** `bool-unite` and `bool-subtract` are
  ignored, so a file that carves geometry rather than tiling it shows its
  pre-Boolean cuboids.
- **Only cuboid primitives are drawn.** Cylinders, spheres, polyhedra and
  extrusions are skipped with a log warning.
- Doping and refinement profiles are parsed but not visualised.
- Only top-level `(define NAME <literal number>)` parameters are editable;
  defines whose value is an expression are derived and recompute automatically.

---

## Usage

1. Open the [live app](https://santhosh111706.github.io/SCM-Geometry-Viewer/).
2. Click **Open .scm file…**, or drag a file straight onto the viewport.
3. Use the toolbar for camera, projection, display mode and opacity.
4. Click a region — in the viewport or in the Regions list — to inspect it.
5. Edit a parameter and press **Apply** to rebuild the model.
6. Press **Save .scm** to download the modified script.

---

## Running locally

The app uses ES modules, so opening `index.html` from a `file://` URL will be
blocked by the browser. Serve the folder over HTTP instead:

```bash
python -m http.server 8000
```

Then open <http://localhost:8000>.

Any static server works — `npx serve`, `php -S localhost:8000`, or a VS Code
Live Server extension.

---

## Project layout

```
.
├── index.html                   markup, toolbar, panels, import map
├── css/style.css                layout, theming, responsive breakpoints
├── js/viewer.js                 parser + evaluator, Three.js scene, UI
└── .github/workflows/deploy.yml GitHub Pages deployment
```

`js/viewer.js` is organised in three parts: **SCM parser** (tokenizer, reader,
evaluator, overlap resolution), **Three.js viewer** (scene building, cameras,
picking) and **user interface** (panels, lists, log, resizers).

There is no `package.json`, no bundler and no dependency install. Three.js
`0.160.0` is loaded from the jsDelivr CDN through an import map declared in
`index.html`; to change the version, update both URLs there together.

---

## Deployment

Every push to `main` triggers `.github/workflows/deploy.yml`, which uploads the
repository root as-is and publishes it to GitHub Pages. Because all asset paths
are relative, the site works from any sub-path.

To enable it on a fork: **Settings → Pages → Build and deployment → Source →
GitHub Actions**.
