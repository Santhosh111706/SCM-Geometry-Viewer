# SCM Geometry Viewer

A browser-based 3D visualization tool for viewing and analyzing **Synopsys Sentaurus Structure Editor (SDE) Scheme (`.scm`) files**.

The SCM Geometry Viewer allows users to upload an SCM file and visually inspect the generated semiconductor device structure, materials, regions, contacts, dimensions, and geometry directly in a web browser.

## Features

- 3D visualization of SDE `.scm` geometry
- Browser-based — no installation required
- Upload and parse `.scm` files
- Visualize semiconductor materials with different colors
- Display device regions and structures
- Select individual regions for inspection
- Inspect region properties and dimensions
- Display electrical contacts
- Show coordinate axes and bounding box
- Control transparency and edge visibility
- Different viewing and projection modes
- Geometry overlap detection and visualization
- Scrollable side panels
- Responsive layout for desktop and mobile devices
- Parsing log for debugging and file analysis
- Works with complex 3D device structures

## Supported Files

The primary supported file format is:

- `.scm` — Sentaurus Structure Editor Scheme file

The viewer is designed primarily for **3D semiconductor device structures generated using Synopsys Sentaurus Structure Editor (SDE)**.

## How It Works

The basic workflow is:

```text
        SCM File
           │
           ▼
     Upload .scm File
           │
           ▼
      Parse Geometry
           │
           ▼
   Extract Regions/Materials
           │
           ▼
     Build 3D Geometry
           │
           ▼
      Interactive Viewer
           │
     ┌─────┴─────┐
     ▼           ▼
  Inspect      Analyze
  Regions      Contacts
