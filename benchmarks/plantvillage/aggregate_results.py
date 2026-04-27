"""Aggregate PlantVillage benchmark results into a markdown table + PNG chart.

Reads every benchmarks/plantvillage/results/*.json produced by benchmark_train.py
and emits two artifacts in the same folder:

  - matrix.md   human-readable markdown table (paste into blog post)
  - matrix.png  scatter: x = wall-time (s), y = post-train IoU,
                dot size proportional to trainable params,
                color keyed by device (mps / cuda / cpu)

Cost column: optional. Pass --gpu-hourly-usd to convert wall-seconds into
a $ figure for cuda rows (e.g. 0.40 for an RTX 4090 on Vast.ai). MPS / CPU
rows are reported as $0 (assumed to be the user's own laptop).

Usage:
  python benchmarks/plantvillage/aggregate_results.py
  python benchmarks/plantvillage/aggregate_results.py --gpu-hourly-usd 0.40
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO / "benchmarks" / "plantvillage" / "results"

DEVICE_COLORS = {"mps": "#1f77b4", "cuda": "#d62728", "cpu": "#2ca02c"}
CONFIG_ORDER = ["vanilla", "lora_r4", "lora_r8", "lora_r16", "lora_r32", "full_decoder"]


@dataclass(frozen=True)
class Row:
    config: str
    variant: str
    device: str
    device_name: str
    n_trainable: int
    pre_iou: float
    post_iou: float
    wall_s: float
    peak_mem_gb: float
    cost_usd: float
    source_file: str

    @property
    def delta_iou(self) -> float:
        return self.post_iou - self.pre_iou


def load_results(results_dir: Path, gpu_hourly_usd: float) -> list[Row]:
    rows: list[Row] = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"[skip] {path.name}: not valid json")
            continue
        meta = data.get("meta", {})
        device = meta.get("device", "?")
        wall = float(data.get("wall_seconds", 0.0))
        cost = (wall / 3600.0) * gpu_hourly_usd if device == "cuda" else 0.0
        rows.append(
            Row(
                config=meta.get("config", "?"),
                variant=meta.get("variant", "?"),
                device=device,
                device_name=meta.get("device_name", "?"),
                n_trainable=int(data.get("n_trainable", 0)),
                pre_iou=float(data.get("pre_train_mean_iou", 0.0)),
                post_iou=float(data.get("post_train_mean_iou", 0.0)),
                wall_s=wall,
                peak_mem_gb=float(data.get("peak_memory_gb", 0.0)),
                cost_usd=cost,
                source_file=path.name,
            )
        )
    return rows


def _config_sort_key(config: str) -> tuple[int, str]:
    try:
        return (CONFIG_ORDER.index(config), config)
    except ValueError:
        return (len(CONFIG_ORDER), config)


def sort_rows(rows: Iterable[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: (r.device, r.variant, _config_sort_key(r.config)))


def render_markdown(rows: list[Row], out: Path, *, gpu_hourly_usd: float) -> None:
    lines: list[str] = []
    lines.append("# PlantVillage SAM2 fine-tune benchmark — aggregated results")
    lines.append("")
    lines.append(f"Total runs: **{len(rows)}**.")
    if any(r.device == "cuda" for r in rows):
        lines.append(f"Cuda cost assumes ${gpu_hourly_usd:.2f}/hour GPU rental.")
    lines.append("")
    lines.append("| device | variant | config | pre IoU | post IoU |   Δ   |  wall  | peak mem | trainable | cost |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in sort_rows(rows):
        lines.append(
            f"| {r.device} | {r.variant} | {r.config} "
            f"| {r.pre_iou:.4f} | {r.post_iou:.4f} | {r.delta_iou:+.4f} "
            f"| {r.wall_s:.0f}s | {r.peak_mem_gb:.2f} GB "
            f"| {r.n_trainable:,} | ${r.cost_usd:.2f} |"
        )
    lines.append("")
    lines.append("## Per-config best result (across devices)")
    best: dict[str, Row] = {}
    for r in rows:
        cur = best.get(r.config)
        if cur is None or r.post_iou > cur.post_iou:
            best[r.config] = r
    lines.append("")
    lines.append("| config | best post IoU | device | wall | trainable |")
    lines.append("|---|---:|---|---:|---:|")
    for cfg in sorted(best, key=_config_sort_key):
        r = best[cfg]
        lines.append(
            f"| {cfg} | {r.post_iou:.4f} | {r.device} | {r.wall_s:.0f}s | {r.n_trainable:,} |"
        )
    lines.append("")
    out.write_text("\n".join(lines))
    print(f"[agg] wrote {out}")


def render_png(rows: list[Row], out: Path) -> None:
    if not rows:
        print("[agg] no rows; skipping png")
        return
    fig, ax = plt.subplots(figsize=(9, 6))
    seen_devices: set[str] = set()
    for r in rows:
        # Dot size: log-ish scaling so vanilla (0) and full_decoder (4M) coexist.
        size = 60 + (max(r.n_trainable, 1) ** 0.5) / 2.0
        color = DEVICE_COLORS.get(r.device, "#7f7f7f")
        label = r.device if r.device not in seen_devices else None
        seen_devices.add(r.device)
        ax.scatter(r.wall_s, r.post_iou, s=size, c=color, alpha=0.75,
                   edgecolors="black", linewidths=0.5, label=label)
        ax.annotate(
            f"{r.config}\n({r.n_trainable:,} p)",
            (r.wall_s, r.post_iou),
            textcoords="offset points",
            xytext=(8, 4),
            fontsize=8,
        )
    ax.set_xlabel("Wall-clock training time (seconds)")
    ax.set_ylabel("Post-train mean IoU")
    ax.set_title("SAM2 fine-tune on PlantVillage — IoU vs training time\n"
                 "(dot size ∝ √trainable params)")
    ax.grid(True, alpha=0.3)
    ax.legend(title="device", loc="lower right")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"[agg] wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    p.add_argument("--gpu-hourly-usd", type=float, default=0.40,
                   help="USD/hour assumed for cuda rows (default 0.40 = RTX 4090 spot)")
    p.add_argument("--out-md", type=Path, default=RESULTS_DIR / "matrix.md")
    p.add_argument("--out-png", type=Path, default=RESULTS_DIR / "matrix.png")
    args = p.parse_args()

    rows = load_results(args.results_dir, args.gpu_hourly_usd)
    if not rows:
        raise SystemExit(f"no result JSONs found under {args.results_dir}")
    render_markdown(rows, args.out_md, gpu_hourly_usd=args.gpu_hourly_usd)
    render_png(rows, args.out_png)


if __name__ == "__main__":
    main()
