// Marching-squares contour extraction for the brush tool.
//
// Reads a painted RGBA canvas, thresholds on alpha to get a binary mask,
// runs marching squares to get edge segments, stitches them into closed
// loops, picks the largest loop by area and simplifies it with the
// Douglas–Peucker algorithm so we don't POST a 5000-vertex polygon.

export type Pt = [number, number];

export function extractLargestPolygon(
  canvas: HTMLCanvasElement,
  alphaThreshold = 64,
  rdpEpsilon = 1.5,
): Pt[] {
  const w = canvas.width;
  const h = canvas.height;
  if (w === 0 || h === 0) return [];
  const ctx = canvas.getContext("2d");
  if (!ctx) return [];
  const data = ctx.getImageData(0, 0, w, h).data;

  // Binary grid (1 if alpha above threshold)
  const g = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) g[i] = data[i * 4 + 3] > alphaThreshold ? 1 : 0;

  return extractLargestPolygonFromMask(g, w, h, rdpEpsilon);
}

// Pure version that takes a binary mask. Exposed for unit testing without
// needing a real DOM canvas.
export function extractLargestPolygonFromMask(
  g: Uint8Array,
  w: number,
  h: number,
  rdpEpsilon = 1.5,
): Pt[] {
  if (w === 0 || h === 0 || g.length !== w * h) return [];

  // Encode grid points at half-cell resolution as ints, so key lookups are
  // O(1) without string churn. Domain is x∈[0,w], y∈[0,h] in 0.5 steps.
  const KSCALE = 2;
  const STRIDE = (w + 2) * KSCALE;
  const ikey = (px: number, py: number) =>
    Math.round(py * KSCALE) * STRIDE + Math.round(px * KSCALE);
  const fromKey = (k: number): Pt => {
    const yi = Math.floor(k / STRIDE);
    const xi = k - yi * STRIDE;
    return [xi / KSCALE, yi / KSCALE];
  };

  const adj = new Map<number, number[]>();
  function addSeg(a: Pt, b: Pt) {
    const ka = ikey(a[0], a[1]);
    const kb = ikey(b[0], b[1]);
    if (ka === kb) return;
    let la = adj.get(ka);
    if (!la) { la = []; adj.set(ka, la); }
    la.push(kb);
    let lb = adj.get(kb);
    if (!lb) { lb = []; adj.set(kb, lb); }
    lb.push(ka);
  }

  const at = (x: number, y: number) =>
    x < 0 || y < 0 || x >= w || y >= h ? 0 : g[y * w + x];

  for (let y = 0; y < h - 1; y++) {
    for (let x = 0; x < w - 1; x++) {
      const tl = at(x, y);
      const tr = at(x + 1, y);
      const br = at(x + 1, y + 1);
      const bl = at(x, y + 1);
      const code = (tl << 3) | (tr << 2) | (br << 1) | bl;
      if (code === 0 || code === 15) continue;
      const top: Pt = [x + 0.5, y];
      const right: Pt = [x + 1, y + 0.5];
      const bot: Pt = [x + 0.5, y + 1];
      const left: Pt = [x, y + 0.5];
      switch (code) {
        case 1: case 14: addSeg(left, bot); break;
        case 2: case 13: addSeg(bot, right); break;
        case 3: case 12: addSeg(left, right); break;
        case 4: case 11: addSeg(top, right); break;
        case 6: case 9:  addSeg(top, bot); break;
        case 7: case 8:  addSeg(left, top); break;
        // Saddle cases — pick the disambiguation that keeps the
        // foreground region connected (treat saddle centre as foreground).
        case 5:  addSeg(left, top); addSeg(bot, right); break;
        case 10: addSeg(top, right); addSeg(left, bot); break;
      }
    }
  }

  // Walk the adjacency map to extract closed loops.
  const visited = new Set<number>();
  const loops: Pt[][] = [];
  for (const startKey of adj.keys()) {
    if (visited.has(startKey)) continue;
    const loop: Pt[] = [];
    let curKey: number | undefined = startKey;
    let prevKey = -1;
    while (curKey !== undefined && !visited.has(curKey)) {
      visited.add(curKey);
      loop.push(fromKey(curKey));
      const nbrs = adj.get(curKey);
      if (!nbrs) break;
      const next: number | undefined = nbrs.find((n) => n !== prevKey);
      prevKey = curKey;
      curKey = next;
    }
    if (loop.length >= 3) loops.push(loop);
  }

  if (loops.length === 0) return [];
  loops.sort((a, b) => polygonArea(b) - polygonArea(a));
  return simplifyClosed(loops[0], rdpEpsilon);
}

export function polygonArea(pts: Pt[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % pts.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

// RDP simplify on a closed polygon: split at the pair of points that are
// farthest apart so the algorithm has a stable "diameter" to work with.
function simplifyClosed(pts: Pt[], eps: number): Pt[] {
  if (pts.length < 4) return pts;
  let imax = 0;
  let dmax = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = distSq(pts[0], pts[i]);
    if (d > dmax) { dmax = d; imax = i; }
  }
  const a = rdpOpen(pts.slice(0, imax + 1), eps);
  const b = rdpOpen(pts.slice(imax).concat([pts[0]]), eps);
  // Drop duplicated joining vertices.
  return [...a.slice(0, -1), ...b.slice(0, -1)];
}

function rdpOpen(pts: Pt[], eps: number): Pt[] {
  if (pts.length < 3) return pts;
  let dmax = 0;
  let idx = 0;
  const a = pts[0];
  const b = pts[pts.length - 1];
  for (let i = 1; i < pts.length - 1; i++) {
    const d = perpDist(pts[i], a, b);
    if (d > dmax) { dmax = d; idx = i; }
  }
  if (dmax > eps) {
    const left = rdpOpen(pts.slice(0, idx + 1), eps);
    const right = rdpOpen(pts.slice(idx), eps);
    return [...left.slice(0, -1), ...right];
  }
  return [a, b];
}

function perpDist(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  if (dx === 0 && dy === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy);
  const cx = a[0] + t * dx;
  const cy = a[1] + t * dy;
  return Math.hypot(p[0] - cx, p[1] - cy);
}

function distSq(a: Pt, b: Pt): number {
  return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2;
}
