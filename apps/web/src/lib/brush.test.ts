import { describe, it, expect } from "vitest";
import { extractLargestPolygonFromMask, polygonArea } from "./brush";

function makeMask(w: number, h: number, fill: (x: number, y: number) => boolean): Uint8Array {
  const g = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) g[y * w + x] = fill(x, y) ? 1 : 0;
  }
  return g;
}

describe("extractLargestPolygonFromMask", () => {
  it("returns empty for an empty mask", () => {
    const g = new Uint8Array(100 * 100);
    expect(extractLargestPolygonFromMask(g, 100, 100)).toEqual([]);
  });

  it("traces a 50x40 axis-aligned rectangle within ~5% area error", () => {
    const w = 200;
    const h = 200;
    // rect from (50,60) to (100,100) → 50 wide, 40 tall, area = 2000
    const g = makeMask(w, h, (x, y) => x >= 50 && x < 100 && y >= 60 && y < 100);
    const poly = extractLargestPolygonFromMask(g, w, h, 0.5);
    expect(poly.length).toBeGreaterThanOrEqual(4);
    expect(poly.length).toBeLessThanOrEqual(20); // RDP must keep it small
    const area = polygonArea(poly);
    expect(area).toBeGreaterThan(2000 * 0.92);
    expect(area).toBeLessThan(2000 * 1.08);
    // bounding box of the returned polygon should be near (50..100, 60..100)
    const xs = poly.map((p) => p[0]);
    const ys = poly.map((p) => p[1]);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(49);
    expect(Math.max(...xs)).toBeLessThanOrEqual(101);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(59);
    expect(Math.max(...ys)).toBeLessThanOrEqual(101);
  });

  it("traces a circle and returns area within 8% of πr²", () => {
    const w = 200;
    const h = 200;
    const cx = 100;
    const cy = 100;
    const r = 40;
    const g = makeMask(w, h, (x, y) => (x - cx) ** 2 + (y - cy) ** 2 <= r * r);
    const poly = extractLargestPolygonFromMask(g, w, h, 1.0);
    const expected = Math.PI * r * r;
    const got = polygonArea(poly);
    expect(got).toBeGreaterThan(expected * 0.92);
    expect(got).toBeLessThan(expected * 1.08);
    // RDP should keep the vertex count manageable
    expect(poly.length).toBeLessThan(80);
    expect(poly.length).toBeGreaterThan(8);
  });

  it("picks the LARGER component when two disjoint regions are painted", () => {
    const w = 200;
    const h = 100;
    const g = makeMask(w, h, (x, y) => {
      // small 10x10 region in top-left, large 60x60 region in bottom-right
      const small = x < 10 && y < 10;
      const large = x >= 120 && x < 180 && y >= 30 && y < 90;
      return small || large;
    });
    const poly = extractLargestPolygonFromMask(g, w, h, 0.5);
    const area = polygonArea(poly);
    // Should match the 60x60 region (3600), NOT the 10x10 (100)
    expect(area).toBeGreaterThan(3600 * 0.9);
    expect(area).toBeLessThan(3600 * 1.1);
  });
});
