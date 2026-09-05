## Web version (GitHub Pages)

A **static, browser-only** build of the viewer lives in [`web/`](web/). It needs
no Python, no Flask, no Node.js and no server: the SCM file is parsed by a
JavaScript port of the parser and rendered with Three.js loaded from a CDN.

**Files you open stay on your machine.** They are read with the browser's
`FileReader` API; nothing is uploaded anywhere.

### Live demo

Once Pages is enabled (below), the app is served at:

```
https://<your-username>.github.io/SCM-Geometry-Viewer/](https://santhosh111706.github.io/SCM-Geometry-Viewer/
```

### Enabling GitHub Pages

1. Push this repository to GitHub.
2. Go to **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
4. Choose branch **main** and folder **/web**.
5. Click **Save**. The first build takes a minute or two.

> If `/web` is not offered in the folder dropdown, GitHub only lists `/` and
> `/docs`. In that case either rename `web/` to `docs/` and select `/docs`, or
> use a GitHub Actions Pages workflow that publishes the `web/` directory.

### Running the web version locally

Because it uses ES modules, opening `index.html` from a `file://` URL will be
blocked by the browser. Serve the folder over HTTP instead:

```bash
cd web
python -m http.server 8000
```

Then open <http://localhost:8000>.

### Desktop vs web

|  | Desktop (`main.py`) | Web (`web/`) |
|---|---|---|
| Runtime | Python + PySide6 + PyVista | browser only |
| Parser | `parser/scm_parser.py` | JavaScript port in `web/js/viewer.js` |
| 3D engine | VTK | Three.js |
| Install | `pip install -r requirements.txt` | none |
| Deployment | run locally | GitHub Pages |

Both parsers were run against the same file and produce identical regions,
coordinates, contacts and parameters. The desktop application is unchanged and
remains the reference implementation.

### What the web version supports

- Open a local `.scm` file, or drag and drop one onto the viewer
- Cuboid regions drawn at their real coordinates, coloured by material
- Camera presets: Front, Back, Left, Right, Top, Bottom, Isometric, Reset
- Perspective and orthographic projection
- Surface and wireframe modes, opacity slider, edge toggle
- Rotate, pan and zoom; click a region to select and inspect it
- Hide or show individual regions and whole materials
- Editable parameters that recompute dependent coordinates and rebuild the model
- Save a modified `.scm` back to disk
- Bounding box, dimensions, material breakdown and quick structural checks
- Parsing log with errors and warnings reported by source line

**Not supported in either version:** Boolean operations (`bool-unite`,
`bool-subtract`) are not evaluated, so a file that carves regions rather than
tiling them will show its pre-Boolean cuboids. Non-cuboid primitives are skipped
with a warning in the log.
