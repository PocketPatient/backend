"""Experiment 1 — Do different speech_styles produce measurably distinct replies?

For each of the 4 diseases, sends the IDENTICAL student message N=10 times
(fresh single-turn conversation each call, fixed persona) and measures:
  - word count (mean/median/std) per style
  - type-token ratio per style
  - ROUGE-L F1 overlap within each style vs. across styles

Run from the backend root:  uv run python eval/speech_style_eval.py
~40 API calls, ~5 minutes at 10 RPM.
"""

from __future__ import annotations

import asyncio
import re
import statistics
from itertools import combinations

import common  # noqa: F401 — must be first: sys.path / cwd / logging setup
from common import DISEASES, PATIENT_AGE, PATIENT_NAME, call_with_retry, logger, save_results

from rouge_score import rouge_scorer

from app.services.llm_gateway import gateway

N_REPLIES = 10
STUDENT_MESSAGE = "Can you tell me what's been going on?"


async def collect_replies() -> list[dict]:
    replies: list[dict] = []
    history = [{"role": "user", "parts": [{"text": STUDENT_MESSAGE}]}]
    for disease in DISEASES:
        for i in range(1, N_REPLIES + 1):
            logger.info("%s (%s): call %d/%d", disease.name, disease.speech_style, i, N_REPLIES)
            text = await call_with_retry(
                lambda d=disease: gateway.generate_patient_message(
                    d, PATIENT_NAME, PATIENT_AGE, history
                ),
                what=f"{disease.name} call {i}",
            )
            replies.append(
                {
                    "disease": disease.name,
                    "speech_style": disease.speech_style,
                    "index": i,
                    "reply": text,
                }
            )
    return replies


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def compute_metrics(replies: list[dict]) -> dict:
    styles = [d.speech_style for d in DISEASES]
    by_style: dict[str, list[str]] = {s: [] for s in styles}
    for r in replies:
        if r["reply"] is not None:
            by_style[r["speech_style"]].append(r["reply"])

    length_stats: dict[str, dict] = {}
    ttr_mean: dict[str, float] = {}
    for style, texts in by_style.items():
        counts = [len(_tokenize(t)) for t in texts]
        tokens = [_tokenize(t) for t in texts]
        length_stats[style] = {
            "n": len(counts),
            "mean": statistics.mean(counts) if counts else None,
            "median": statistics.median(counts) if counts else None,
            "std": statistics.stdev(counts) if len(counts) > 1 else None,
        }
        ttrs = [len(set(tk)) / len(tk) for tk in tokens if tk]
        ttr_mean[style] = statistics.mean(ttrs) if ttrs else None

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)

    def _f1(a: str, b: str) -> float:
        return scorer.score(a, b)["rougeL"].fmeasure

    # Symmetric styles x styles matrix: diagonal = mean pairwise F1 within a
    # style (10C2 = 45 pairs), off-diagonal = mean F1 over all cross pairs.
    matrix: list[list[float | None]] = []
    for si in styles:
        row: list[float | None] = []
        for sj in styles:
            if si == sj:
                pairs = [_f1(a, b) for a, b in combinations(by_style[si], 2)]
            else:
                pairs = [_f1(a, b) for a in by_style[si] for b in by_style[sj]]
            row.append(statistics.mean(pairs) if pairs else None)
        matrix.append(row)

    return {
        "length_stats": length_stats,
        "ttr_mean": ttr_mean,
        "rouge_matrix": {"styles": styles, "matrix": matrix},
    }


def print_metrics(metrics: dict) -> None:
    print("\n=== Word count per reply ===")
    print(f"{'style':<14}{'n':>4}{'mean':>10}{'median':>10}{'std':>10}")
    for style, s in metrics["length_stats"].items():
        std = f"{s['std']:.1f}" if s["std"] is not None else "-"
        print(f"{style:<14}{s['n']:>4}{s['mean']:>10.1f}{s['median']:>10.1f}{std:>10}")

    print("\n=== Type-token ratio (mean per style) ===")
    for style, ttr in metrics["ttr_mean"].items():
        print(f"{style:<14}{ttr:.3f}")

    styles = metrics["rouge_matrix"]["styles"]
    matrix = metrics["rouge_matrix"]["matrix"]
    print("\n=== ROUGE-L F1 (diagonal = within-style, off-diagonal = across) ===")
    print(" " * 14 + "".join(f"{s:>14}" for s in styles))
    for si, row in zip(styles, matrix):
        cells = "".join(f"{v:>14.3f}" if v is not None else f"{'-':>14}" for v in row)
        print(f"{si:<14}{cells}")


async def main() -> None:
    replies = await collect_replies()
    n_failed = sum(1 for r in replies if r["reply"] is None)
    if n_failed:
        logger.warning("%d/%d calls failed and were recorded as null", n_failed, len(replies))
    metrics = compute_metrics(replies)
    save_results(
        "speech_style",
        {
            "student_message": STUDENT_MESSAGE,
            "persona": {"name": PATIENT_NAME, "age": PATIENT_AGE},
            "n_per_style": N_REPLIES,
            "replies": replies,
            "metrics": metrics,
        },
    )
    print_metrics(metrics)


if __name__ == "__main__":
    asyncio.run(main())
