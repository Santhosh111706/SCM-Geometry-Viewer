/* ==========================================================================
   SCM Geometry Viewer - static web edition
   --------------------------------------------------------------------------
   Everything runs in the browser. Files are read with the FileReader API and
   never leave the machine: there is no upload, no fetch to any server, and no
   backend of any kind. That is what makes the app deployable on GitHub Pages.

   This file has three parts:

     PART 1   SCM parser        a small Scheme reader and evaluator, ported
                                from the Python desktop version
     PART 2   Three.js viewer   scene building, cameras, picking
     PART 3   User interface    panels, lists, log, resizers

   Why a real evaluator rather than regular expressions
   ---------------------------------------------------
   Real SDE scripts define helper procedures and call them:

       (define (hfo2-collar tag st ya yb zha zca zcb zhb)
         (sdegeo:create-cuboid ...)
         (sdegeo:create-cuboid ...))

       (hfo2-collar "n" "s1" ya1 yb1 znh0 znc0 znc1 znh1)

   A regex scanner sees one create-cuboid and misses the twelve the procedure
   actually produces. Evaluating the file gives the true region list, and
   resolves every dependent variable the way SDE would.
   ========================================================================== */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';


/* ==========================================================================
   PART 1 - SCM PARSER
   ========================================================================== */

/* ---------------------------------------------------------------- colours */
/* Kept numerically identical to MATERIAL_COLORS in models/region.py so the
   web viewer and the desktop viewer colour the same file the same way. */
const MATERIAL_COLORS = {
  'Silicon':     [0.25, 0.45, 0.85],
  'HfO2':        [0.95, 0.65, 0.15],
  'SiO2':        [0.60, 0.90, 0.95],
  'Si3N4':       [0.30, 0.75, 0.35],
  'TiN':         [0.55, 0.57, 0.60],
  'PolySilicon': [0.80, 0.40, 0.70],
  'Aluminum':    [0.75, 0.75, 0.78],
  'Copper':      [0.85, 0.55, 0.35],
  'Germanium':   [0.40, 0.35, 0.70],
  'Air':         [0.90, 0.90, 0.90],
  'Gas':         [0.90, 0.90, 0.90],
};
const DEFAULT_COLOR = [0.70, 0.70, 0.70];

/* Substrate-like regions get a darker shade of their material colour, so the
   bulk reads differently from the active silicon even though both are
   "Silicon". Same rule as the Python model. */
const DARK_PREFIXES = ['substrate', 'sub_', 'bulk', 'handle'];
const DARK_FACTOR = 0.55;

/* Materials the parser has no colour for. Reported once so the log stays
   readable rather than repeating per region. */
const unknownMaterials = new Set();

function toHex(rgb) {
  const c = rgb.map((v) => {
    const n = Math.max(0, Math.min(255, Math.round(v * 255)));
    return n.toString(16).padStart(2, '0');
  });
  return '#' + c.join('');
}

export function materialColor(material, regionName = '') {
  const base = MATERIAL_COLORS[material] || DEFAULT_COLOR;
  if (!MATERIAL_COLORS[material]) unknownMaterials.add(material);
  const low = String(regionName).toLowerCase();
  const dark = DARK_PREFIXES.some((p) => low.startsWith(p) || low.includes('_' + p));
  return toHex(dark ? base.map((v) => v * DARK_FACTOR) : base);
}

/* ---------------------------------------------------------------- tokenizer */
function tokenize(text) {
  const tokens = [];
  let i = 0, line = 1;
  const n = text.length;

  while (i < n) {
    const c = text[i];

    if (c === '\n') { line++; i++; continue; }
    if (c === ' ' || c === '\t' || c === '\r' || c === '\f') { i++; continue; }

    // ; line comment
    if (c === ';') { while (i < n && text[i] !== '\n') i++; continue; }

    // #| block comment |#
    if (c === '#' && text[i + 1] === '|') {
      i += 2;
      while (i + 1 < n && !(text[i] === '|' && text[i + 1] === '#')) {
        if (text[i] === '\n') line++;
        i++;
      }
      i += 2;
      continue;
    }

    if (c === '(' || c === '[') { tokens.push({ k: '(', line }); i++; continue; }
    if (c === ')' || c === ']') { tokens.push({ k: ')', line }); i++; continue; }
    if (c === "'")              { tokens.push({ k: 'quote', line }); i++; continue; }

    // string literal
    if (c === '"') {
      i++;
      let buf = '';
      while (i < n && text[i] !== '"') {
        if (text[i] === '\\' && i + 1 < n) {
          const nx = text[i + 1];
          buf += ({ n: '\n', t: '\t', '\\': '\\', '"': '"' })[nx] ?? nx;
          i += 2;
          continue;
        }
        if (text[i] === '\n') line++;
        buf += text[i++];
      }
      i++;
      tokens.push({ k: 'string', v: buf, line });
      continue;
    }

    // bare atom
    const start = i;
    while (i < n && !' \t\r\n\f()[];"'.includes(text[i])) i++;
    if (i === start) i++;                       // never stall
    tokens.push({ k: 'atom', v: text.slice(start, i), line });
  }
  return tokens;
}

/* ---------------------------------------------------------------- reader */
class Sym extends String {}                      // distinguishes symbol from string

function atomValue(raw) {
  if (raw === '#t') return true;
  if (raw === '#f') return false;
  if (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(raw)) {
    const v = parseFloat(raw);
    if (!Number.isNaN(v)) return v;
  }
  return new Sym(raw);
}

class ReaderError extends Error {
  constructor(msg, line) { super(msg); this.line = line; }
}

export function readForms(text) {
  const tokens = tokenize(text);
  let pos = 0;

  function read() {
    if (pos >= tokens.length) throw new ReaderError('unexpected end of input');
    const t = tokens[pos];

    if (t.k === 'quote') { pos++; const f = [new Sym('quote'), read()]; f.line = t.line; return f; }

    if (t.k === '(') {
      pos++;
      const out = [];
      out.line = t.line;
      for (;;) {
        if (pos >= tokens.length) throw new ReaderError("unbalanced '(' - missing ')'", t.line);
        if (tokens[pos].k === ')') { pos++; return out; }
        out.push(read());
      }
    }

    if (t.k === ')') throw new ReaderError("unexpected ')'", t.line);

    pos++;
    return t.k === 'string' ? t.v : atomValue(t.v);
  }

  const forms = [];
  while (pos < tokens.length) forms.push(read());
  return forms;
}

/* ---------------------------------------------------------------- environment */
class Env {
  constructor(params = [], args = [], parent = null) {
    this.vars = new Map();
    this.parent = parent;
    params.forEach((p, k) => this.vars.set(p, args[k]));
  }
  lookup(name) {
    let e = this;
    while (e) { if (e.vars.has(name)) return e.vars.get(name); e = e.parent; }
    throw new Error(`undefined variable '${name}'`);
  }
  has(name) {
    let e = this;
    while (e) { if (e.vars.has(name)) return true; e = e.parent; }
    return false;
  }
  set(name, value) { this.vars.set(name, value); }
  setExisting(name, value) {
    let e = this;
    while (e) { if (e.vars.has(name)) { e.vars.set(name, value); return; } e = e.parent; }
    this.vars.set(name, value);
  }
}

class Procedure {
  constructor(params, body, env, name = 'lambda') {
    this.params = params; this.body = body; this.env = env; this.name = name;
  }
}

/* ---------------------------------------------------------------- builtins */
const num = (x) => {
  if (typeof x !== 'number' || Number.isNaN(x)) throw new Error(`expected a number, got ${x}`);
  return x;
};

function baseBuiltins() {
  return new Map(Object.entries({
    '+': (...a) => a.reduce((s, v) => s + num(v), 0),
    '-': (a, ...r) => (r.length === 0 ? -num(a) : r.reduce((s, v) => s - num(v), num(a))),
    '*': (...a) => a.reduce((s, v) => s * num(v), 1),
    '/': (a, ...r) => {
      if (r.length === 0) return 1 / num(a);
      return r.reduce((s, v) => {
        if (num(v) === 0) throw new Error('division by zero');
        return s / v;
      }, num(a));
    },
    'min': (...a) => Math.min(...a.map(num)),
    'max': (...a) => Math.max(...a.map(num)),
    'abs': (a) => Math.abs(num(a)),
    'sqrt': (a) => Math.sqrt(num(a)),
    'expt': (a, b) => Math.pow(num(a), num(b)),
    'exp': (a) => Math.exp(num(a)),
    'log': (a) => Math.log(num(a)),
    'floor': (a) => Math.floor(num(a)),
    'ceiling': (a) => Math.ceil(num(a)),
    'round': (a) => Math.round(num(a)),
    'truncate': (a) => Math.trunc(num(a)),
    'modulo': (a, b) => ((num(a) % num(b)) + num(b)) % num(b),
    'remainder': (a, b) => num(a) % num(b),

    '=':  (a, b) => num(a) === num(b),
    '<':  (a, b) => num(a) < num(b),
    '>':  (a, b) => num(a) > num(b),
    '<=': (a, b) => num(a) <= num(b),
    '>=': (a, b) => num(a) >= num(b),
    'not': (a) => a === false,
    'eq?': (a, b) => a === b || String(a) === String(b),
    'equal?': (a, b) => String(a) === String(b),
    'zero?': (a) => num(a) === 0,
    'positive?': (a) => num(a) > 0,
    'negative?': (a) => num(a) < 0,
    'number?': (a) => typeof a === 'number',
    'string?': (a) => typeof a === 'string',
    'null?': (a) => a === null || (Array.isArray(a) && a.length === 0),

    'string-append': (...a) => a.map(String).join(''),
    'string-length': (s) => String(s).length,
    'number->string': (x) => String(x),
    'string->number': (s) => parseFloat(s),
    'symbol->string': (s) => String(s),

    'list': (...a) => a,
    'car': (l) => l[0],
    'cdr': (l) => l.slice(1),
    'cons': (a, l) => [a, ...(Array.isArray(l) ? l : [l])],
    'length': (l) => l.length,
    'append': (...ls) => [].concat(...ls),
    'reverse': (l) => l.slice().reverse(),
    'list-ref': (l, i) => l[i | 0],

    'display': () => null,       // this is a parser, not a REPL
    'newline': () => null,
  }));
}

/* ---------------------------------------------------------------- evaluator */
const MAX_DEPTH = 400;

class Evaluator {
  constructor(hooks, onUnknown, onError) {
    this.global = new Env();
    baseBuiltins().forEach((v, k) => this.global.set(k, v));
    this.hooks = hooks || {};
    this.onUnknown = onUnknown || (() => {});
    this.onError = onError || (() => {});
    this.unknownSeen = new Set();
    this.depth = 0;
    this.overrides = {};
  }

  run(forms, overrides = {}) {
    this.overrides = overrides || {};
    for (const form of forms) {
      try {
        this.eval(form, this.global);
      } catch (err) {
        const line = Array.isArray(form) ? form.line : undefined;
        const head = Array.isArray(form) && form.length ? String(form[0]) : '?';
        this.onError(`${err.name || 'Error'} while evaluating (${head} ...): ${err.message}`, line);
      }
    }
    return this.global;
  }

  eval(x, env) {
    if (x instanceof Sym) {
      const name = String(x);
      if (!env.has(name)) { this.onError(`undefined variable '${name}'`, undefined); return null; }
      return env.lookup(name);
    }
    if (!Array.isArray(x)) return x;             // number, string, boolean
    if (x.length === 0) return null;

    const line = x.line;
    const op = x[0];

    if (op instanceof Sym) {
      const name = String(op);

      switch (name) {
        case 'quote':  return x[1];
        case 'define': return this.doDefine(x, env);
        case 'lambda': return new Procedure(x[1].map(String), x.slice(2), env);
        case 'set!': {
          const v = this.eval(x[2], env);
          env.setExisting(String(x[1]), v);
          return v;
        }
        case 'if': {
          const t = this.eval(x[1], env);
          if (t !== false && t !== null && t !== undefined) return this.eval(x[2], env);
          return x.length > 3 ? this.eval(x[3], env) : null;
        }
        case 'when': {
          const t = this.eval(x[1], env);
          return (t !== false && t !== null) ? this.evalBody(x.slice(2), env) : null;
        }
        case 'unless': {
          const t = this.eval(x[1], env);
          return (t === false || t === null) ? this.evalBody(x.slice(2), env) : null;
        }
        case 'cond': {
          for (const cl of x.slice(1)) {
            if (cl[0] instanceof Sym && String(cl[0]) === 'else') return this.evalBody(cl.slice(1), env);
            const t = this.eval(cl[0], env);
            if (t !== false && t !== null) return cl.length > 1 ? this.evalBody(cl.slice(1), env) : t;
          }
          return null;
        }
        case 'and': {
          let out = true;
          for (const f of x.slice(1)) { out = this.eval(f, env); if (out === false || out === null) return false; }
          return out;
        }
        case 'or': {
          for (const f of x.slice(1)) { const o = this.eval(f, env); if (o !== false && o !== null) return o; }
          return false;
        }
        case 'begin': return this.evalBody(x.slice(1), env);
        case 'let':
        case 'let*':
        case 'letrec': {
          const inner = new Env([], [], env);
          for (const b of x[1]) inner.set(String(b[0]), this.eval(b[1], name === 'let' ? env : inner));
          return this.evalBody(x.slice(2), inner);
        }
        default: break;
      }
    }

    const args = x.slice(1).map((a) => this.eval(a, env));

    if (op instanceof Sym) {
      const name = String(op);
      if (Object.prototype.hasOwnProperty.call(this.hooks, name)) {
        return this.hooks[name](args, line);
      }
      if (env.has(name)) return this.apply(env.lookup(name), args);
      if (!this.unknownSeen.has(name)) {
        this.unknownSeen.add(name);
        this.onUnknown(name, line);
      }
      return null;
    }

    return this.apply(this.eval(op, env), args);
  }

  apply(fn, args) {
    if (fn instanceof Procedure) {
      if (++this.depth > MAX_DEPTH) {
        this.depth--;
        throw new Error('procedure nesting too deep (possible infinite recursion)');
      }
      try {
        return this.evalBody(fn.body, new Env(fn.params, args, fn.env));
      } finally {
        this.depth--;
      }
    }
    if (typeof fn === 'function') return fn(...args);
    if (fn === null || fn === undefined) return null;
    throw new Error('attempt to call a non-procedure');
  }

  evalBody(body, env) {
    let out = null;
    for (const f of body) out = this.eval(f, env);
    return out;
  }

  doDefine(x, env) {
    const target = x[1];

    // (define (name a b) body...)
    if (Array.isArray(target)) {
      const name = String(target[0]);
      env.set(name, new Procedure(target.slice(1).map(String), x.slice(2), env, name));
      return null;
    }

    // (define name value)
    const name = String(target);
    if (env === this.global && Object.prototype.hasOwnProperty.call(this.overrides, name)) {
      env.set(name, Number(this.overrides[name]));
      return null;
    }
    env.set(name, x.length > 2 ? this.eval(x[2], env) : null);
    return null;
  }
}

/* ---------------------------------------------------------------- SDE hooks */

/* Commands that are real SDE but have no effect on the geometry we draw.
   Listing them keeps the log free of noise. */
const SILENTLY_IGNORED = new Set([
  'sde:clear', 'sde:set-process-up-direction', 'sdegeo:set-default-boolean',
  'sde:build-mesh', 'sde:save-model', 'sde:setrefprop', 'sde:add-material',
  'sdedr:define-constant-profile', 'sdedr:define-gaussian-profile',
  'sdedr:define-erf-profile', 'sdedr:define-analytical-profile',
  'sdedr:define-refinement-size', 'sdedr:define-refinement-placement',
  'sdedr:define-refinement-function', 'sdedr:define-refinement-region',
  'sdedr:define-constant-profile-placement', 'sdedr:define-constant-profile-material',
  'sdedr:define-constant-profile-region', 'sdedr:define-analytical-profile-placement',
  'sdedr:define-profile-placement',
  'sdeio:save-tdr-bnd', 'sdeio:load-tdr-bnd', 'sdegeo:delete-region',
]);

class Position extends Array {}
class FaceRef { constructor(point) { this.point = point; } }
class RGB extends Array {}

/**
 * Parse SCM source into { regions, contacts, windows, parameters, messages }.
 *
 * @param {string} text        file contents
 * @param {object} overrides   optional { paramName: numericValue }
 */
export function parseScm(text, overrides = {}) {
  const result = {
    regions: [], contacts: [], windows: [],
    parameters: {}, messages: [], sourceText: text,
  };
  const log = (level, msg, line) => result.messages.push({ level, text: msg, line });

  unknownMaterials.clear();

  let forms;
  try {
    forms = readForms(text);
  } catch (err) {
    log('error', `could not read the file: ${err.message}`, err.line);
    return finish(result, log);
  }
  log('info', `read ${forms.length} top-level forms`);

  let currentContact = null;
  const nameSeen = new Map();

  const hooks = {
    'position': (a, line) => {
      if (a.length < 3 || a.some((v) => typeof v !== 'number')) {
        log('error', `malformed (position ${a.join(' ')}): coordinates must be numbers`, line);
        return null;
      }
      return Position.from(a.slice(0, 3));
    },

    'color:rgb': (a) => (a.length >= 3 ? RGB.from(a.slice(0, 3)) : null),

    'find-face-id': (a, line) => {
      if (a[0] instanceof Position) return new FaceRef(a[0]);
      log('warning', 'find-face-id called without a (position ...)', line);
      return null;
    },
    'find-body-id': (a, line) => (a[0] instanceof Position ? new FaceRef(a[0]) : null),

    'sdegeo:create-cuboid': (a, line) => {
      const [p0, p1, material, name] = a;
      if (!(p0 instanceof Position) || !(p1 instanceof Position)) {
        log('error', `create-cuboid for region '${name ?? '?'}': a corner is not a valid (position x y z)`, line);
        return null;
      }
      if (typeof material !== 'string') {
        log('error', `create-cuboid at line ${line ?? '?'}: missing or invalid material name`, line);
        return null;
      }
      let rname = name;
      if (typeof rname !== 'string') {
        rname = `region_${result.regions.length + 1}`;
        log('warning', `create-cuboid with no region name; called it '${rname}'`, line);
      }

      // SDE accepts corners either way round; normalise and report a swap
      let [x0, y0, z0] = p0, [x1, y1, z1] = p1;
      let swapped = false;
      if (x0 > x1) { [x0, x1] = [x1, x0]; swapped = true; }
      if (y0 > y1) { [y0, y1] = [y1, y0]; swapped = true; }
      if (z0 > z1) { [z0, z1] = [z1, z0]; swapped = true; }
      if (swapped) log('warning', `region '${rname}': corners were given in reverse order, normalised`, line);

      const lx = x1 - x0, ly = y1 - y0, lz = z1 - z0;
      if (lx <= 0 || ly <= 0 || lz <= 0) {
        log('error',
          `region '${rname}' has a zero or negative dimension (${fmtNum(lx)} x ${fmtNum(ly)} x ${fmtNum(lz)}); not drawn`,
          line);
        return null;
      }

      if (nameSeen.has(rname)) {
        log('warning', `duplicate region name '${rname}' (first seen at line ${nameSeen.get(rname)}); both kept`, line);
      } else {
        nameSeen.set(rname, line);
      }

      const r = {
        name: rname, material, x0, y0, z0, x1, y1, z1,
        lx, ly, lz,
        volume: lx * ly * lz,
        center: [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2],
        color: materialColor(material, rname),
        line: line ?? null,
      };
      result.regions.push(r);
      return r;
    },

    'sdegeo:define-contact-set': (a, line) => {
      const name = String(a[0]);
      const rgb = a.find((v) => v instanceof RGB);
      const c = { name, position: null, color: rgb ? toHex(Array.from(rgb)) : null, regionName: null, face: null, line: line ?? null };
      result.contacts.push(c);
      return c;
    },

    'sdegeo:set-current-contact-set': (a) => { currentContact = String(a[0]); return null; },

    'sdegeo:set-contact-faces': (a, line) => {
      let faces = a[0];
      const target = (typeof a[1] === 'string') ? a[1] : currentContact;
      if (!target) { log('warning', 'set-contact-faces with no contact name and no current contact set', line); return null; }

      if (faces instanceof FaceRef) faces = [faces];
      if (!Array.isArray(faces)) faces = [faces];
      const pts = faces.filter((f) => f instanceof FaceRef).map((f) => f.point);
      if (!pts.length) { log('warning', `contact '${target}': no usable face pick point found`, line); return null; }

      const existing = result.contacts.find((c) => c.name === target);
      if (existing) {
        existing.position = Array.from(pts[0]);
        if (existing.line === null) existing.line = line ?? null;
      } else {
        result.contacts.push({ name: target, position: Array.from(pts[0]), color: null, regionName: null, face: null, line: line ?? null });
        log('warning', `contact '${target}' had faces set before it was defined`, line);
      }
      return null;
    },

    'sdedr:define-refinement-window': (a, line) => {
      const [name, kind, p0, p1] = a;
      if (!(p0 instanceof Position) || !(p1 instanceof Position)) {
        log('warning', `refinement window '${name}': unsupported form '${kind}', skipped`, line);
        return null;
      }
      result.windows.push({
        name: String(name),
        x0: Math.min(p0[0], p1[0]), x1: Math.max(p0[0], p1[0]),
        y0: Math.min(p0[1], p1[1]), y1: Math.max(p0[1], p1[1]),
        z0: Math.min(p0[2], p1[2]), z1: Math.max(p0[2], p1[2]),
      });
      return null;
    },
  };

  const ev = new Evaluator(
    hooks,
    (name, line) => {
      if (!SILENTLY_IGNORED.has(name)) {
        log('warning', `unsupported command '${name}' was ignored; geometry parsing continued`, line);
      }
    },
    (msg, line) => log('error', msg, line)
  );

  try {
    ev.run(forms, overrides);
  } catch (err) {
    log('error', `aborted: ${err.message}`);
  }

  // Editable parameters are top-level (define NAME <literal number>).
  // Defines whose value is an expression are derived and recompute on the
  // next pass, so editing them directly would be meaningless.
  const re = /^[ \t]*\(define\s+([A-Za-z_][\w:!?*/+\-<>=]*)\s+(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\)/gm;
  let m;
  while ((m = re.exec(text)) !== null) result.parameters[m[1]] = parseFloat(m[2]);

  return finish(result, log);
}

function finish(result, log) {
  // resolve each contact pick point to a region and a face direction
  for (const c of result.contacts) {
    if (!c.position) continue;
    const [px, py, pz] = c.position;
    const eps = 1e-9;
    for (const r of result.regions) {
      if (px < r.x0 - eps || px > r.x1 + eps ||
          py < r.y0 - eps || py > r.y1 + eps ||
          pz < r.z0 - eps || pz > r.z1 + eps) continue;
      const tests = [
        [px, r.x0, r.x1, '-X', '+X'],
        [py, r.y0, r.y1, '-Y', '+Y'],
        [pz, r.z0, r.z1, '-Z', '+Z'],
      ];
      let face = 'interior';
      for (const [v, lo, hi, neg, pos] of tests) {
        if (Math.abs(v - lo) < eps) { face = neg; break; }
        if (Math.abs(v - hi) < eps) { face = pos; break; }
      }
      c.regionName = r.name;
      c.face = face;
      break;
    }
  }

  if (!result.regions.length) {
    log('warning', 'no cuboid regions were created; check that the file uses sdegeo:create-cuboid');
  }
  if (unknownMaterials.size) {
    log('warning', `no colour defined for material(s): ${[...unknownMaterials].join(', ')} - drawn in grey`);
  }
  const unattached = result.contacts.filter((c) => !c.position).map((c) => c.name);
  if (unattached.length) {
    log('warning', `${unattached.length} contact set(s) declared but never attached to a face: ${unattached.join(', ')}`);
  }

  result.materials = [...new Set(result.regions.map((r) => r.material))].sort();
  result.bounds = result.regions.length ? [
    Math.min(...result.regions.map((r) => r.x0)), Math.max(...result.regions.map((r) => r.x1)),
    Math.min(...result.regions.map((r) => r.y0)), Math.max(...result.regions.map((r) => r.y1)),
    Math.min(...result.regions.map((r) => r.z0)), Math.max(...result.regions.map((r) => r.z1)),
  ] : null;
  result.errors = result.messages.filter((m) => m.level === 'error');
  result.warnings = result.messages.filter((m) => m.level === 'warning');
  return result;
}

/** Rewrite the source with edited scalar defines, leaving all else intact. */
export function writeModifiedScm(text, params) {
  let out = text;
  for (const [name, value] of Object.entries(params)) {
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(
      `(^[ \\t]*\\(define\\s+${esc}\\s+)(-?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?)(\\s*\\))`, 'gm');
    if (re.test(out)) {
      re.lastIndex = 0;
      out = out.replace(re, (_m, a, _b, c) => `${a}${trimNum(value)}${c}`);
    } else {
      out = `(define ${name} ${trimNum(value)})\n` + out;
    }
  }
  return out;
}

/* ---------------------------------------------------------------- number fmt */
function fmtNum(v, digits = 6) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  if (typeof v !== 'number') return String(v);
  return String(Number(v.toPrecision(digits)));
}
function trimNum(v) {
  return String(Number(Number(v).toPrecision(10)));
}


/* ==========================================================================
   PART 2 - THREE.JS VIEWER
   ========================================================================== */

const state = {
  model: null,
  baseline: {},
  hiddenMaterials: new Set(),
  hiddenRegions: new Set(),
  selectedRegion: null,
  opacity: 1,
  style: 'surface',
  showEdges: true,
  showContacts: true,
  showAxes: true,
  showBBox: false,
  fileName: '',
};

let renderer, scene, camera, perspCam, orthoCam, controls;
let modelGroup, regionGroup, edgeGroup, contactGroup, axesGroup, bboxGroup, highlight;
let raycaster, pointer;
const meshByName = new Map();
const edgeByName = new Map();
let unitBox, unitEdges;

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function initThree() {
  const host = $('#viewport');

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(host.clientWidth, Math.max(host.clientHeight, 1));
  host.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x11151a);

  const aspect = host.clientWidth / Math.max(host.clientHeight, 1);
  perspCam = new THREE.PerspectiveCamera(45, aspect, 0.01, 5000);
  perspCam.position.set(21, 17, 25);

  const d = 16;
  orthoCam = new THREE.OrthographicCamera(-d * aspect, d * aspect, d, -d, -2000, 5000);
  orthoCam.position.set(21, 17, 25);

  camera = perspCam;

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;

  scene.add(new THREE.AmbientLight(0xffffff, 0.62));
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(1, 1.4, 1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x9db4d0, 0.35);
  fill.position.set(-1, -0.6, -0.8);
  scene.add(fill);

  modelGroup = new THREE.Group();
  regionGroup = new THREE.Group();
  edgeGroup = new THREE.Group();
  contactGroup = new THREE.Group();
  axesGroup = new THREE.Group();
  bboxGroup = new THREE.Group();
  modelGroup.add(regionGroup, edgeGroup, contactGroup, bboxGroup);
  scene.add(modelGroup, axesGroup);

  unitBox = new THREE.BoxGeometry(1, 1, 1);
  unitEdges = new THREE.EdgesGeometry(unitBox);

  highlight = new THREE.LineSegments(
    unitEdges, new THREE.LineBasicMaterial({ color: 0xffe14d }));
  highlight.visible = false;
  modelGroup.add(highlight);

  raycaster = new THREE.Raycaster();
  pointer = new THREE.Vector2();

  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('resize', onResize);
  new ResizeObserver(onResize).observe(host);

  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  })();
}

function onResize() {
  const host = $('#viewport');
  if (!host) return;
  const w = host.clientWidth, h = Math.max(host.clientHeight, 1);
  renderer.setSize(w, h);
  const aspect = w / h;
  perspCam.aspect = aspect;
  perspCam.updateProjectionMatrix();
  const d = orthoCam.top;
  orthoCam.left = -d * aspect;
  orthoCam.right = d * aspect;
  orthoCam.updateProjectionMatrix();
}

function clearScene() {
  for (const g of [regionGroup, edgeGroup, contactGroup, axesGroup, bboxGroup]) {
    while (g.children.length) {
      const c = g.children.pop();
      if (c.geometry && c.geometry !== unitBox && c.geometry !== unitEdges) c.geometry.dispose();
      if (c.material) {
        if (c.material.map) c.material.map.dispose();
        c.material.dispose();
      }
    }
  }
  meshByName.clear();
  edgeByName.clear();
  highlight.visible = false;
}

/* SCM coordinates are micrometres, so a device spans ~0.08 units. Numbers
   that small give poor depth precision in WebGL. The model is therefore
   drawn inside a group with ONE UNIFORM scale factor, which changes no
   proportion and no relative position. The inspector always reports the
   original micrometre values. */
function buildScene(model) {
  clearScene();
  if (!model || !model.regions.length) { $('#scale-note').classList.remove('on'); return; }

  const b = model.bounds;
  const spans = [b[1] - b[0], b[3] - b[2], b[5] - b[4]];
  const maxSpan = Math.max(...spans) || 1;
  const s = 20 / maxSpan;
  const cx = (b[0] + b[1]) / 2, cy = (b[2] + b[3]) / 2, cz = (b[4] + b[5]) / 2;

  for (const r of model.regions) {
    const mat = new THREE.MeshLambertMaterial({
      color: new THREE.Color(r.color),
      transparent: true,
      opacity: state.opacity,
      wireframe: state.style === 'wireframe',
      side: THREE.DoubleSide,
      depthWrite: state.opacity >= 0.99,
    });
    const mesh = new THREE.Mesh(unitBox, mat);
    mesh.scale.set(r.lx * s, r.ly * s, r.lz * s);
    mesh.position.set((r.center[0] - cx) * s, (r.center[1] - cy) * s, (r.center[2] - cz) * s);
    mesh.userData.region = r;
    mesh.visible = regionVisible(r);
    regionGroup.add(mesh);
    meshByName.set(r.name, mesh);

    const e = new THREE.LineSegments(unitEdges,
      new THREE.LineBasicMaterial({ color: 0x0d1116, transparent: true, opacity: 0.85 }));
    e.scale.copy(mesh.scale);
    e.position.copy(mesh.position);
    e.visible = mesh.visible && state.showEdges && state.style === 'surface';
    edgeGroup.add(e);
    edgeByName.set(r.name, e);
  }

  // contacts
  const cr = 0.32;
  for (const c of model.contacts) {
    if (!c.position) continue;
    const p = new THREE.Vector3(
      (c.position[0] - cx) * s, (c.position[1] - cy) * s, (c.position[2] - cz) * s);
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(cr, 20, 14),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(c.color || '#ff2d55') }));
    marker.position.copy(p);
    marker.userData.contact = c;
    marker.renderOrder = 3;
    contactGroup.add(marker);

    const label = makeLabel(c.name);
    label.position.set(p.x, p.y + cr * 2.8, p.z);
    label.renderOrder = 4;
    label.userData.contact = c;
    contactGroup.add(label);
  }
  contactGroup.visible = state.showContacts;

  // overall bounding box
  const bb = new THREE.LineSegments(unitEdges,
    new THREE.LineBasicMaterial({ color: 0x5c7089 }));
  bb.scale.set(spans[0] * s, spans[1] * s, spans[2] * s);
  bb.position.set(0, 0, 0);
  bboxGroup.add(bb);
  bboxGroup.visible = state.showBBox;

  buildAxes();

  $('#scale-note').textContent =
    `view scale ${fmtNum(s, 3)}x uniform | 1 world unit = ${fmtNum(1 / s, 3)} um`;
  $('#scale-note').classList.add('on');

  resetCamera();
}

function makeLabel(text) {
  const font = 34, pad = 8;
  const c = document.createElement('canvas');
  let g = c.getContext('2d');
  g.font = `600 ${font}px monospace`;
  c.width = g.measureText(text).width + pad * 2;
  c.height = font + pad * 2;

  g = c.getContext('2d');
  g.fillStyle = 'rgba(18,23,29,0.88)';
  g.strokeStyle = 'rgba(255,90,90,0.9)';
  g.lineWidth = 3;
  if (g.roundRect) {
    g.beginPath(); g.roundRect(1.5, 1.5, c.width - 3, c.height - 3, 7); g.fill(); g.stroke();
  } else {
    g.fillRect(1.5, 1.5, c.width - 3, c.height - 3);
    g.strokeRect(1.5, 1.5, c.width - 3, c.height - 3);
  }
  g.font = `600 ${font}px monospace`;
  g.fillStyle = '#f2f6fa';
  g.textBaseline = 'middle';
  g.fillText(text, pad, c.height / 2);

  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  sp.scale.set((c.width / c.height) * 1.1, 1.1, 1);
  return sp;
}

function buildAxes() {
  const L = 12, o = -12;
  const helper = new THREE.AxesHelper(L);      // X red, Y green, Z blue
  helper.position.set(o, o, o);
  axesGroup.add(helper);

  const tag = (txt, pos, colour) => {
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const g = c.getContext('2d');
    g.font = '700 44px monospace';
    g.fillStyle = colour;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(txt, 32, 34);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c), transparent: true, depthTest: false }));
    sp.position.copy(pos);
    sp.scale.set(1.7, 1.7, 1);
    axesGroup.add(sp);
  };
  tag('X', new THREE.Vector3(o + L + 1, o, o), '#ff6b6b');
  tag('Y', new THREE.Vector3(o, o + L + 1, o), '#63d471');
  tag('Z', new THREE.Vector3(o, o, o + L + 1), '#6b9bff');
  axesGroup.visible = state.showAxes;
}

/* ---------------------------------------------------------------- visibility */
function regionVisible(r) {
  return !state.hiddenMaterials.has(r.material) && !state.hiddenRegions.has(r.name);
}

function applyVisibility() {
  if (!state.model) return;
  for (const r of state.model.regions) {
    const vis = regionVisible(r);
    const m = meshByName.get(r.name);
    const e = edgeByName.get(r.name);
    if (m) m.visible = vis;
    if (e) e.visible = vis && state.showEdges && state.style === 'surface';
  }
  renderRegionList();
  renderMaterials();
}

function applyStyle() {
  meshByName.forEach((m) => {
    m.material.opacity = state.opacity;
    m.material.wireframe = state.style === 'wireframe';
    m.material.depthWrite = state.opacity >= 0.99;
    m.material.needsUpdate = true;
  });
  edgeByName.forEach((e, name) => {
    const m = meshByName.get(name);
    e.visible = (m ? m.visible : false) && state.showEdges && state.style === 'surface';
  });
}

/* ---------------------------------------------------------------- camera */
function setProjection(kind) {
  const wasPersp = camera === perspCam;
  if (kind === 'orthographic' && wasPersp) {
    orthoCam.position.copy(perspCam.position);
    orthoCam.quaternion.copy(perspCam.quaternion);
    camera = orthoCam;
  } else if (kind === 'perspective' && !wasPersp) {
    perspCam.position.copy(orthoCam.position);
    perspCam.quaternion.copy(orthoCam.quaternion);
    camera = perspCam;
  } else return;
  controls.object = camera;
  controls.update();
  onResize();
}

function setView(which) {
  const R = 36;
  const table = {
    front:  [0, 0, R], back: [0, 0, -R],
    right:  [R, 0, 0], left: [-R, 0, 0],
    top:    [0, R, 0], bottom: [0, -R, 0],
    iso:    [R * 0.62, R * 0.5, R * 0.62],
  };
  const p = table[which] || table.iso;
  camera.up.set(0, 1, 0);
  if (which === 'top' || which === 'bottom') camera.up.set(0, 0, -1);
  camera.position.set(p[0], p[1], p[2]);
  controls.target.set(0, 0, 0);
  camera.lookAt(0, 0, 0);
  controls.update();
}

function resetCamera() { setView('iso'); }

/* ---------------------------------------------------------------- picking */
function onPointerDown(ev) {
  if (ev.button !== 0) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);

  if (state.showContacts) {
    const hc = raycaster.intersectObjects(contactGroup.children, false)
      .find((h) => h.object.userData.contact);
    if (hc) { selectContact(hc.object.userData.contact.name); switchTab('tab-contacts'); return; }
  }

  const hits = raycaster.intersectObjects(regionGroup.children, false).filter((h) => h.object.visible);
  selectRegion(hits.length ? hits[0].object.userData.region.name : null);
}


/* ==========================================================================
   PART 3 - USER INTERFACE
   ========================================================================== */

function log(text, level = 'info') {
  const body = $('#log-body');
  const s = document.createElement('span');
  s.className = 'log-' + level;
  s.textContent = text + '\n';
  body.appendChild(s);
  body.scrollTop = body.scrollHeight;
}

/* ---------------------------------------------------------------- loading */
function loadFile(file) {
  const reader = new FileReader();
  reader.onerror = () => log(`[ERROR] could not read ${file.name}`, 'error');
  reader.onload = () => {
    state.fileName = file.name;
    state.sourceText = String(reader.result);
    state.hiddenMaterials.clear();
    state.hiddenRegions.clear();
    state.selectedRegion = null;
    log(`\n=== reading ${file.name} (${(file.size / 1024).toFixed(1)} kB, locally) ===`);
    runParse(state.sourceText, {}, true);
  };
  reader.readAsText(file);
}

function runParse(text, overrides, isFirst) {
  let model;
  const t0 = performance.now();
  try {
    model = parseScm(text, overrides);
  } catch (err) {
    log(`[ERROR] parser crashed: ${err.message}`, 'error');
    return;
  }
  const ms = performance.now() - t0;

  state.model = model;
  if (isFirst) state.baseline = { ...model.parameters };

  for (const m of model.messages) {
    log(`[${m.level.toUpperCase()}]${m.line ? ` (line ${m.line})` : ''} ${m.text}`, m.level);
  }
  log(`--- ${model.regions.length} regions, ${model.errors.length} errors, ` +
      `${model.warnings.length} warnings, parsed in ${ms.toFixed(0)} ms ---`,
      model.errors.length ? 'error' : 'ok');

  $('#file-name').textContent = state.fileName || '(unnamed)';
  $('#log-counts').textContent =
    `${model.regions.length} regions | ${model.errors.length} errors | ${model.warnings.length} warnings`;
  $('#viewer-hint').style.display = model.regions.length ? 'none' : '';

  renderParameters();
  renderMaterials();
  renderRegionList();
  renderContacts();
  renderModelTab();
  fillRegionInfo(null);

  buildScene(model);
  applyStyle();

  for (const id of ['#btn-apply', '#btn-reset', '#btn-save', '#btn-hide-selected', '#btn-show-all']) {
    $(id).disabled = false;
  }
}

/* ---------------------------------------------------------------- panels */
function renderParameters() {
  const body = $('#param-body');
  body.innerHTML = '';
  const params = state.model ? state.model.parameters : {};
  const names = Object.keys(params).sort();
  if (!names.length) {
    body.innerHTML = '<tr><td colspan="2" class="muted pad">no scalar parameters found</td></tr>';
    return;
  }
  for (const name of names) {
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    td1.textContent = name;
    const td2 = document.createElement('td');
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = String(params[name]);
    inp.dataset.name = name;
    inp.addEventListener('input', () => {
      const base = state.baseline[name];
      inp.classList.toggle('changed', base !== undefined && parseFloat(inp.value) !== base);
    });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyParameters(); });
    td2.appendChild(inp);
    tr.append(td1, td2);
    body.appendChild(tr);
  }
}

function renderMaterials() {
  const ul = $('#material-list');
  ul.innerHTML = '';
  if (!state.model || !state.model.materials.length) {
    ul.innerHTML = '<li class="muted pad">no materials</li>';
    return;
  }
  for (const name of state.model.materials) {
    const regions = state.model.regions.filter((r) => r.material === name);
    const li = document.createElement('li');

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !state.hiddenMaterials.has(name);
    cb.addEventListener('change', () => {
      if (cb.checked) state.hiddenMaterials.delete(name);
      else state.hiddenMaterials.add(name);
      applyVisibility();
    });

    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = materialColor(name);

    const nm = document.createElement('span');
    nm.className = 'rname';
    nm.textContent = name;

    const ct = document.createElement('span');
    ct.className = 'rcount';
    ct.textContent = regions.length;

    li.append(cb, sw, nm, ct);
    ul.appendChild(li);
  }
}

function renderRegionList() {
  const ul = $('#region-list');
  ul.innerHTML = '';
  if (!state.model || !state.model.regions.length) {
    ul.innerHTML = '<li class="muted pad">no regions</li>';
    $('#region-count').textContent = '';
    return;
  }
  const filter = $('#region-filter').value.trim().toLowerCase();
  let shown = 0;

  for (const r of state.model.regions) {
    if (filter && !r.name.toLowerCase().includes(filter)) continue;
    shown++;
    const li = document.createElement('li');
    li.dataset.name = r.name;
    li.classList.toggle('selected', r.name === state.selectedRegion);
    li.classList.toggle('is-hidden', !regionVisible(r));

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !state.hiddenRegions.has(r.name);
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      if (cb.checked) state.hiddenRegions.delete(r.name);
      else state.hiddenRegions.add(r.name);
      applyVisibility();
    });

    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = r.color;

    const nm = document.createElement('span');
    nm.className = 'rname';
    nm.textContent = r.name;
    nm.title = r.name;

    const mt = document.createElement('span');
    mt.className = 'rmat';
    mt.textContent = r.material;

    li.append(cb, sw, nm, mt);
    li.addEventListener('click', () => selectRegion(r.name, false));
    ul.appendChild(li);
  }
  $('#region-count').textContent =
    filter ? `(${shown} of ${state.model.regions.length})` : `(${state.model.regions.length})`;
}

function renderContacts() {
  const ul = $('#contact-list');
  ul.innerHTML = '';
  if (!state.model || !state.model.contacts.length) {
    ul.innerHTML = '<li class="muted pad">no contacts</li>';
    return;
  }
  for (const c of state.model.contacts) {
    const li = document.createElement('li');
    li.className = 'clickable';
    li.dataset.name = c.name;
    li.innerHTML = `<b>${escapeHtml(c.name)}</b> <span class="muted">` +
      `${escapeHtml(c.regionName || 'unresolved')}${c.face ? ' &middot; ' + c.face : ''}</span>`;
    li.addEventListener('click', () => selectContact(c.name));
    ul.appendChild(li);
  }
}

function renderModelTab() {
  const m = state.model;
  const setField = (sel, f, v) => {
    const el = document.querySelector(`${sel} [data-f="${f}"]`);
    if (el) el.textContent = v;
  };
  if (!m || !m.bounds) {
    ['bx','by','bz','sx','sy','sz','aspect','vol'].forEach((f) => setField('#model-bounds', f, '-'));
    return;
  }
  const b = m.bounds;
  const sx = b[1] - b[0], sy = b[3] - b[2], sz = b[5] - b[4];
  const mx = Math.max(sx, sy, sz), mn = Math.min(sx, sy, sz);

  setField('#model-bounds', 'bx', `${fmtNum(b[0])} .. ${fmtNum(b[1])}`);
  setField('#model-bounds', 'by', `${fmtNum(b[2])} .. ${fmtNum(b[3])}`);
  setField('#model-bounds', 'bz', `${fmtNum(b[4])} .. ${fmtNum(b[5])}`);
  setField('#model-bounds', 'sx', fmtNum(sx));
  setField('#model-bounds', 'sy', fmtNum(sy));
  setField('#model-bounds', 'sz', fmtNum(sz));
  setField('#model-bounds', 'aspect', `${fmtNum(mx / (mn || 1), 3)} : 1`);
  setField('#model-bounds', 'vol', fmtNum(m.regions.reduce((s, r) => s + r.volume, 0), 4));

  setField('#model-counts', 'regions', m.regions.length);
  setField('#model-counts', 'materials', m.materials.length);
  setField('#model-counts', 'contacts', m.contacts.length);
  setField('#model-counts', 'params', Object.keys(m.parameters).length);
  setField('#model-counts', 'errors', m.errors.length);
  setField('#model-counts', 'warnings', m.warnings.length);

  // material breakdown
  const bd = $('#material-breakdown');
  bd.innerHTML = '';
  for (const name of m.materials) {
    const rs = m.regions.filter((r) => r.material === name);
    const vol = rs.reduce((s, r) => s + r.volume, 0);
    const li = document.createElement('li');
    li.innerHTML =
      `<span class="swatch" style="background:${materialColor(name)}"></span>` +
      `<span class="rname">${escapeHtml(name)}</span>` +
      `<span class="rcount">${rs.length} &middot; ${fmtNum(vol, 3)}</span>`;
    bd.appendChild(li);
  }

  // quick structural checks
  const qc = $('#quick-checks');
  qc.innerHTML = '';
  const add = (kind, text) => {
    const li = document.createElement('li');
    const cls = kind === 'ok' ? 'badge-ok' : kind === 'warn' ? 'badge-warn' : 'badge-err';
    const lbl = kind === 'ok' ? 'OK' : kind === 'warn' ? 'WARN' : 'FAIL';
    li.innerHTML = `<span class="badge ${cls}">${lbl}</span><span>${escapeHtml(text)}</span>`;
    qc.appendChild(li);
  };

  const thin = m.regions.filter((r) => Math.min(r.lx, r.ly, r.lz) < 1e-4);
  add(thin.length ? 'warn' : 'ok',
      thin.length ? `${thin.length} very thin region(s)` : 'no sliver regions');

  const names = m.regions.map((r) => r.name);
  const dupes = names.filter((n, i) => names.indexOf(n) !== i);
  add(dupes.length ? 'warn' : 'ok',
      dupes.length ? `${new Set(dupes).size} duplicate region name(s)` : 'all region names unique');

  // overlap test, capped so a huge file cannot freeze the tab
  if (m.regions.length <= 600) {
    let ov = 0;
    for (let i = 0; i < m.regions.length; i++) {
      for (let j = i + 1; j < m.regions.length; j++) {
        const a = m.regions[i], c = m.regions[j];
        if (Math.min(a.x1, c.x1) - Math.max(a.x0, c.x0) > 1e-12 &&
            Math.min(a.y1, c.y1) - Math.max(a.y0, c.y0) > 1e-12 &&
            Math.min(a.z1, c.z1) - Math.max(a.z0, c.z0) > 1e-12) ov++;
      }
    }
    add(ov ? 'err' : 'ok',
        ov ? `${ov} overlapping region pair(s)` : 'no overlapping volumes');
  } else {
    add('warn', `overlap test skipped (${m.regions.length} regions)`);
  }

  const unres = m.contacts.filter((c) => !c.position || !c.regionName);
  if (m.contacts.length) {
    add(unres.length ? 'warn' : 'ok',
        unres.length ? `${unres.length} contact(s) unattached or unresolved`
                     : `all ${m.contacts.length} contacts on a region face`);
  }
}

/* ---------------------------------------------------------------- selection */
function selectRegion(name, scrollTo = true) {
  state.selectedRegion = name;
  if (name && state.model) {
    const mesh = meshByName.get(name);
    if (mesh) {
      highlight.scale.copy(mesh.scale).multiplyScalar(1.01);
      highlight.position.copy(mesh.position);
      highlight.visible = true;
    }
    const r = state.model.regions.find((x) => x.name === name);
    $('#picked-label').textContent = `${r.name}  [${r.material}]`;
    $('#picked-label').classList.add('on');
    fillRegionInfo(r);
    switchTab('tab-region');
  } else {
    highlight.visible = false;
    $('#picked-label').classList.remove('on');
    fillRegionInfo(null);
  }
  $$('#region-list li').forEach((li) => {
    const on = li.dataset.name === name;
    li.classList.toggle('selected', on);
    if (on && scrollTo) li.scrollIntoView({ block: 'nearest' });
  });
}

function fillRegionInfo(r) {
  const set = (f, v) => {
    const el = document.querySelector(`#region-info [data-f="${f}"]`);
    if (el) el.textContent = v;
  };
  const fields = ['name','material','x0','x1','y0','y1','z0','z1','lx','ly','lz','volume','line'];
  if (!r) { fields.forEach((f) => set(f, '-')); return; }
  set('name', r.name);
  set('material', r.material);
  ['x0','x1','y0','y1','z0','z1','lx','ly','lz'].forEach((f) => set(f, fmtNum(r[f])));
  set('volume', fmtNum(r.volume, 4));
  set('line', r.line ?? '-');
}

function selectContact(name) {
  const c = state.model.contacts.find((x) => x.name === name);
  $$('#contact-list li').forEach((li) => li.classList.toggle('selected', li.dataset.name === name));
  if (!c) return;
  const pos = c.position ? '(' + c.position.map((v) => fmtNum(v)).join(', ') + ')' : 'not attached';
  const box = $('#contact-detail');
  box.classList.remove('muted');
  box.innerHTML =
    `<b>${escapeHtml(c.name)}</b><br>` +
    `region: ${escapeHtml(c.regionName || 'unresolved')}<br>` +
    `coordinate: ${pos}<br>` +
    `face direction: ${c.face || '-'}<br>` +
    `source line: ${c.line ?? '-'}`;
  if (c.regionName) selectRegion(c.regionName);
}

function switchTab(id) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === id));
  $$('.tab-page').forEach((p) => p.classList.toggle('active', p.id === id));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------------------------------------------------------- parameters */
function collectParameters() {
  const out = {};
  const bad = [];
  $$('#param-body input').forEach((inp) => {
    const v = parseFloat(inp.value);
    if (Number.isNaN(v)) bad.push(inp.dataset.name);
    else out[inp.dataset.name] = v;
  });
  return { out, bad };
}

function applyParameters() {
  if (!state.model) return;
  const { out, bad } = collectParameters();
  if (bad.length) { log(`[ERROR] not a number: ${bad.join(', ')}`, 'error'); return; }
  const changed = Object.keys(out).filter(
    (k) => state.baseline[k] !== undefined && out[k] !== state.baseline[k]);
  log(`\n=== re-parsing with ${changed.length} changed parameter(s): ` +
      `${changed.length ? changed.join(', ') : 'none'} ===`);
  runParse(state.sourceText, out, false);
}

function resetParameters() {
  if (!state.model) return;
  log('\n=== parameters reset to file values ===');
  runParse(state.sourceText, {}, true);
}

function saveScm() {
  if (!state.model) return;
  const { out, bad } = collectParameters();
  if (bad.length) { log(`[ERROR] not a number: ${bad.join(', ')}`, 'error'); return; }
  const text = writeModifiedScm(state.sourceText, out);
  const base = state.fileName.replace(/\.[^.]+$/, '') || 'model';
  const name = `${base}_modified.scm`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`[INFO] downloaded ${name}`, 'ok');
}

/* ---------------------------------------------------------------- resizers */
function initStackResizers() {
  const sections = [$('#sec-parameters'), $('#sec-materials'), $('#sec-regions')];
  const MIN = 74;
  $$('#left-stack .v-resizer').forEach((handle) => {
    handle.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      const i = parseInt(handle.dataset.resize, 10);
      const above = sections[i], below = sections[i + 1];
      const startY = ev.clientY;
      const hA = above.getBoundingClientRect().height;
      const total = hA + below.getBoundingClientRect().height;
      sections.forEach((s) => { s.style.flexBasis = s.getBoundingClientRect().height + 'px'; });
      document.body.classList.add('resizing');
      handle.setPointerCapture(ev.pointerId);

      const move = (e) => {
        const newA = Math.max(MIN, Math.min(total - MIN, hA + (e.clientY - startY)));
        above.style.flexBasis = newA + 'px';
        below.style.flexBasis = (total - newA) + 'px';
      };
      const up = () => {
        document.body.classList.remove('resizing');
        handle.releasePointerCapture(ev.pointerId);
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
    });
  });
}

function initSideResizers() {
  const drag = (handle, panel, side) => {
    if (!handle) return;
    handle.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      const startX = ev.clientX;
      const startW = panel.getBoundingClientRect().width;
      document.body.classList.add('resizing');
      handle.setPointerCapture(ev.pointerId);
      const move = (e) => {
        const delta = side === 'left' ? (e.clientX - startX) : (startX - e.clientX);
        panel.style.width = Math.max(210, Math.min(700, startW + delta)) + 'px';
        onResize();
      };
      const up = () => {
        document.body.classList.remove('resizing');
        handle.releasePointerCapture(ev.pointerId);
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
    });
  };
  drag($('#resize-left'), $('#left-panel'), 'left');
  drag($('#resize-right'), $('#right-panel'), 'right');
}

function initLogResizer() {
  const handle = $('#resize-bottom');
  const panel = $('#log-panel');
  handle.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const startY = ev.clientY;
    const startH = panel.getBoundingClientRect().height;
    document.body.classList.add('resizing');
    handle.setPointerCapture(ev.pointerId);
    const move = (e) => {
      panel.style.height =
        Math.max(56, Math.min(window.innerHeight - 200, startH - (e.clientY - startY))) + 'px';
      onResize();
    };
    const up = () => {
      document.body.classList.remove('resizing');
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
}

/* ---------------------------------------------------------------- wiring */
function initUI() {
  $('#file-input').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (f) loadFile(f);
    e.target.value = '';                 // allow re-picking the same file
  });

  // drag and drop onto the viewport
  const vp = $('#viewport');
  ['dragenter', 'dragover'].forEach((t) =>
    vp.addEventListener(t, (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }));
  vp.addEventListener('drop', (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) loadFile(f);
  });

  $('#btn-apply').addEventListener('click', applyParameters);
  $('#btn-reset').addEventListener('click', resetParameters);
  $('#btn-save').addEventListener('click', saveScm);
  $('#btn-clear-log').addEventListener('click', () => { $('#log-body').innerHTML = ''; });
  $('#region-filter').addEventListener('input', renderRegionList);

  $('#btn-hide-selected').addEventListener('click', () => {
    if (state.selectedRegion) { state.hiddenRegions.add(state.selectedRegion); applyVisibility(); }
  });
  $('#btn-show-all').addEventListener('click', () => {
    state.hiddenRegions.clear();
    state.hiddenMaterials.clear();
    applyVisibility();
  });

  $$('#viewer-toolbar [data-view]').forEach((b) =>
    b.addEventListener('click', () => setView(b.dataset.view)));
  $('#btn-reset-cam').addEventListener('click', resetCamera);

  $('#sel-projection').addEventListener('change', (e) => setProjection(e.target.value));
  $('#sel-style').addEventListener('change', (e) => { state.style = e.target.value; applyStyle(); });
  $('#opacity').addEventListener('input', (e) => { state.opacity = e.target.value / 100; applyStyle(); });
  $('#chk-edges').addEventListener('change', (e) => { state.showEdges = e.target.checked; applyStyle(); });
  $('#chk-contacts').addEventListener('change', (e) => {
    state.showContacts = e.target.checked; contactGroup.visible = state.showContacts;
  });
  $('#chk-axes').addEventListener('change', (e) => {
    state.showAxes = e.target.checked; axesGroup.visible = state.showAxes;
  });
  $('#chk-bbox').addEventListener('change', (e) => {
    state.showBBox = e.target.checked; bboxGroup.visible = state.showBBox;
  });

  $$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));

  // small-screen panel toggle
  $('#btn-panels').addEventListener('click', () => {
    $('#left-panel').classList.toggle('collapsed');
    $('#right-panel').classList.toggle('collapsed');
    onResize();
  });

  initStackResizers();
  initSideResizers();
  initLogResizer();
}

/* ---------------------------------------------------------------- boot */
if (typeof document !== 'undefined' && document.getElementById('viewport')) {
  initThree();
  initUI();
  log('[INFO] viewer ready - open a .scm file to begin');
  log('[INFO] files are read locally in your browser and are never uploaded');
}