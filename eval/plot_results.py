"""Generate publication-ready figures (PNG, 300 DPI, no titles — LaTeX captions
will supply them) from the latest eval results JSONs.

Run from the backend root:  uv run python eval/plot_results.py
Outputs land next to the JSONs in eval/results/.
"""

from __future__ import annotations

import json
import re

import common  # noqa: F401 — must be first: sys.path / cwd / logging setup
from common import DISEASES, RESULTS_DIR, latest_results, logger

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# Validated categorical palette (light mode), one slot per disease/style, fixed
# order matching common.DISEASES. Aqua/yellow are sub-3:1 on white, so every
# figure keeps visible text labels naming each series (relief rule).
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]

# Single-hue sequential ramp (blue, light→dark) for the ROUGE heatmap.
SEQUENTIAL_STEPS = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]

INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

plt.rcParams.update(
    {
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
        "font.size": 9,
        "text.color": INK,
        "axes.edgecolor": BASELINE,
        "axes.labelcolor": INK_SECONDARY,
        "axes.linewidth": 0.8,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "xtick.labelcolor": INK_SECONDARY,
        "ytick.labelcolor": INK_SECONDARY,
        "grid.color": GRIDLINE,
        "grid.linewidth": 0.6,
        "legend.frameon": False,
    }
)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _despine(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot_speech_length(data: dict) -> None:
    styles = [d.speech_style for d in DISEASES]
    counts = {
        s: [
            len(_tokenize(r["reply"]))
            for r in data["replies"]
            if r["speech_style"] == s and r["reply"] is not None
        ]
        for s in styles
    }
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    boxes = ax.boxplot(
        [counts[s] for s in styles],
        tick_labels=styles,
        patch_artist=True,
        widths=0.55,
        boxprops={"linewidth": 0.8},
        whiskerprops={"color": BASELINE, "linewidth": 0.8},
        capprops={"color": BASELINE, "linewidth": 0.8},
        medianprops={"color": INK, "linewidth": 1.2},
        flierprops={
            "marker": "o", "markersize": 3,
            "markerfacecolor": "none", "markeredgecolor": INK_MUTED,
        },
    )
    for patch, color in zip(boxes["boxes"], SERIES_COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)
        patch.set_edgecolor(color)
    ax.set_ylabel("Words per reply")
    ax.set_xlabel("Speech style")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    _despine(ax)
    out = RESULTS_DIR / "fig_speech_length.png"
    fig.savefig(out)
    plt.close(fig)
    logger.info("Wrote %s", out)


def plot_rouge_matrix(data: dict) -> None:
    styles = data["metrics"]["rouge_matrix"]["styles"]
    matrix = data["metrics"]["rouge_matrix"]["matrix"]
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQUENTIAL_STEPS)
    fig, ax = plt.subplots(figsize=(3.8, 3.2))
    vals = [v for row in matrix for v in row if v is not None]
    vmin, vmax = min(vals), max(vals)
    im = ax.imshow(matrix, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(styles)), styles, rotation=30, ha="right")
    ax.set_yticks(range(len(styles)), styles)
    for i in range(len(styles)):
        for j in range(len(styles)):
            v = matrix[i][j]
            if v is None:
                continue
            # dark ink on light cells, white on dark cells
            frac = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            ax.text(
                j, i, f"{v:.2f}", ha="center", va="center",
                color="#ffffff" if frac > 0.55 else INK, fontsize=8,
            )
    for spine in ax.spines.values():
        spine.set_visible(False)
    cbar = fig.colorbar(im, ax=ax, shrink=0.85)
    cbar.set_label("ROUGE-L F1", color=INK_SECONDARY)
    cbar.ax.tick_params(color=INK_MUTED, labelcolor=INK_SECONDARY)
    cbar.outline.set_visible(False)
    out = RESULTS_DIR / "fig_rouge_matrix.png"
    fig.savefig(out)
    plt.close(fig)
    logger.info("Wrote %s", out)


def plot_consistency(data: dict) -> None:
    records = data["records"]
    bucket_ends = [10, 20, 30, 40]
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    for disease, color in zip(DISEASES, SERIES_COLORS):
        ys = []
        for end in bucket_ends:
            recs = [
                r for r in records
                if r["disease"] == disease.name and r["turn"] <= end and r["reply"] is not None
            ]
            breaks = sum(1 for r in recs if r["break_reason"] is not None)
            ys.append(100.0 * breaks / len(recs) if recs else 0.0)
        ax.plot(
            bucket_ends, ys,
            color=color, linewidth=2, marker="o", markersize=5,
            label=disease.name,
        )
    ax.set_xlabel("Interview turn")
    ax.set_ylabel("Cumulative character-break rate (%)")
    ax.set_xticks(bucket_ends, [f"1–{e}" for e in bucket_ends])
    ax.set_ylim(bottom=0)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    _despine(ax)
    ax.legend(fontsize=7, loc="best")
    out = RESULTS_DIR / "fig_consistency.png"
    fig.savefig(out)
    plt.close(fig)
    logger.info("Wrote %s", out)


def main() -> None:
    speech_path = latest_results("speech_style")
    if speech_path:
        data = json.loads(speech_path.read_text())
        logger.info("Speech-style results: %s", speech_path.name)
        plot_speech_length(data)
        plot_rouge_matrix(data)
    else:
        logger.warning("No speech_style results found — run speech_style_eval.py first")

    consistency_path = latest_results("consistency")
    if consistency_path:
        data = json.loads(consistency_path.read_text())
        logger.info("Consistency results: %s", consistency_path.name)
        plot_consistency(data)
    else:
        logger.warning("No consistency results found — run consistency_eval.py first")


if __name__ == "__main__":
    main()
