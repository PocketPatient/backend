"""Experiment 2 — Does the patient stay in character over a long conversation?

A scripted 40-question clinical interview, 5 independent trials per disease.
Each patient reply is checked with the RAW character guardrail
(check_character_break, no retry/fallback orchestration) and recorded.

4 diseases x 5 trials x 40 turns = 800 calls, ~80 minutes at 10 RPM.
Progress is checkpointed after every turn to eval/results/consistency.checkpoint.json;
rerunning offers to resume from where a previous run stopped.

Run from the backend root:  uv run python eval/consistency_eval.py [--resume|--fresh]
"""

from __future__ import annotations

import asyncio
import json
import sys

import common  # noqa: F401 — must be first: sys.path / cwd / logging setup
from common import (
    DISEASES,
    PATIENT_AGE,
    PATIENT_NAME,
    RESULTS_DIR,
    call_with_retry,
    logger,
    save_results,
)

from app.services.character_guardrail import check_character_break
from app.services.llm_gateway import gateway

N_TRIALS = 5
CHECKPOINT_PATH = RESULTS_DIR / "consistency.checkpoint.json"

# Stand-in for a failed (null) reply so the conversation can continue with a
# valid alternating history; recorded reply stays null in the results.
NULL_REPLY_PLACEHOLDER = "..."

INTERVIEW_QUESTIONS = [
    "Can you tell me what's been going on lately?",
    "When did you first notice something was wrong?",
    "How has your sleep been?",
    "Do you have trouble falling asleep, staying asleep, or both?",
    "How would you describe your mood most days?",
    "Have you noticed any changes in your appetite?",
    "Have you lost or gained any weight recently?",
    "How is your energy level during the day?",
    "Are you still enjoying the things you used to enjoy?",
    "How is your concentration — can you focus on reading or watching TV?",
    "How are things going at work or school?",
    "Have you missed any days of work recently because of how you're feeling?",
    "How are your relationships with your family?",
    "Do you have friends you can talk to about what's going on?",
    "Do you drink alcohol? How much in a typical week?",
    "Do you use any recreational drugs?",
    "How much caffeine do you have in a day?",
    "Are you taking any medications right now?",
    "Have you ever seen a psychiatrist or a therapist before?",
    "Has anyone in your family had mental health problems?",
    "Have you ever had thoughts of hurting yourself?",
    "Do you ever feel like life isn't worth living?",
    "Have you ever had periods where you felt unusually great or full of energy?",
    "During those times, did you need less sleep than usual?",
    "Do you ever hear or see things that other people don't seem to notice?",
    "Do you ever feel like people are watching you or out to get you?",
    "How do you usually cope when you're feeling stressed?",
    "Do you worry a lot? What kinds of things do you worry about?",
    "Do you ever have sudden episodes of intense fear or panic?",
    "Any physical symptoms — headaches, stomach problems, racing heart?",
    "How has all of this been affecting your daily routine?",
    "Are you able to keep up with things at home — cooking, cleaning, bills?",
    "Have people around you commented on changes in your behavior?",
    "What does a typical day look like for you right now?",
    "Have you had any major life changes or losses recently?",
    "Is there anything that makes you feel better, even briefly?",
    "Is there anything that makes things worse?",
    "What worries you most about what you're experiencing?",
    "What are you hoping to get out of talking with me?",
    "Is there anything else you think I should know?",
]
N_TURNS = len(INTERVIEW_QUESTIONS)  # 40

BUCKETS = [(1, 10), (11, 20), (21, 30), (31, 40)]


def _load_checkpoint() -> list[dict]:
    if not CHECKPOINT_PATH.exists():
        return []
    records = json.loads(CHECKPOINT_PATH.read_text())["records"]
    logger.info("Checkpoint found with %d/%d completed turns", len(records), 4 * N_TRIALS * N_TURNS)
    return records


def _write_checkpoint(records: list[dict]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False))


def _rebuild_history(trial_records: list[dict]) -> list[dict]:
    """Reconstruct the alternating conversation history for a partial trial."""
    history: list[dict] = []
    for rec in trial_records:
        history.append({"role": "user", "parts": [{"text": INTERVIEW_QUESTIONS[rec["turn"] - 1]}]})
        reply = rec["reply"] if rec["reply"] is not None else NULL_REPLY_PLACEHOLDER
        history.append({"role": "model", "parts": [{"text": reply}]})
    return history


async def run_trials(records: list[dict]) -> list[dict]:
    total = len(DISEASES) * N_TRIALS * N_TURNS
    for d_idx, disease in enumerate(DISEASES):
        for trial in range(1, N_TRIALS + 1):
            trial_start = (d_idx * N_TRIALS + (trial - 1)) * N_TURNS
            if len(records) >= trial_start + N_TURNS:
                continue  # trial fully done in a previous run
            done_turns = max(0, len(records) - trial_start)
            history = _rebuild_history(records[trial_start : trial_start + done_turns])
            if done_turns:
                logger.info("Resuming %s trial %d at turn %d", disease.name, trial, done_turns + 1)
            for turn in range(done_turns + 1, N_TURNS + 1):
                logger.info(
                    "%s: trial %d/%d, turn %d/%d (%d/%d overall)",
                    disease.name, trial, N_TRIALS, turn, N_TURNS, len(records) + 1, total,
                )
                history.append(
                    {"role": "user", "parts": [{"text": INTERVIEW_QUESTIONS[turn - 1]}]}
                )
                text = await call_with_retry(
                    lambda d=disease, h=history: gateway.generate_patient_message(
                        d, PATIENT_NAME, PATIENT_AGE, h
                    ),
                    what=f"{disease.name} trial {trial} turn {turn}",
                )
                break_reason = check_character_break(text, disease.name) if text else None
                records.append(
                    {
                        "disease": disease.name,
                        "speech_style": disease.speech_style,
                        "trial": trial,
                        "turn": turn,
                        "reply": text,
                        "break_reason": break_reason,
                    }
                )
                history.append(
                    {
                        "role": "model",
                        "parts": [{"text": text if text is not None else NULL_REPLY_PLACEHOLDER}],
                    }
                )
                _write_checkpoint(records)
    return records


def compute_metrics(records: list[dict]) -> dict:
    def rate(recs: list[dict]) -> dict:
        valid = [r for r in recs if r["reply"] is not None]
        breaks = [r for r in valid if r["break_reason"] is not None]
        return {
            "replies": len(valid),
            "breaks": len(breaks),
            "break_rate": len(breaks) / len(valid) if valid else None,
            "nulls": len(recs) - len(valid),
        }

    per_disease: dict[str, dict] = {}
    for disease in DISEASES:
        d_recs = [r for r in records if r["disease"] == disease.name]
        buckets = {
            f"{lo}-{hi}": rate([r for r in d_recs if lo <= r["turn"] <= hi])
            for lo, hi in BUCKETS
        }
        per_disease[disease.name] = {"buckets": buckets, "overall": rate(d_recs)}

    overall_buckets = {
        f"{lo}-{hi}": rate([r for r in records if lo <= r["turn"] <= hi]) for lo, hi in BUCKETS
    }
    reasons: dict[str, int] = {}
    for r in records:
        if r["break_reason"]:
            reasons[r["break_reason"]] = reasons.get(r["break_reason"], 0) + 1
    return {
        "per_disease": per_disease,
        "overall_buckets": overall_buckets,
        "overall": rate(records),
        "break_reasons": reasons,
    }


def print_metrics(metrics: dict) -> None:
    bucket_names = [f"{lo}-{hi}" for lo, hi in BUCKETS]
    print("\n=== Character-break rate by turn bucket (breaks/replies) ===")
    print(f"{'disease':<32}" + "".join(f"{b:>14}" for b in bucket_names) + f"{'overall':>14}")
    for name, d in metrics["per_disease"].items():
        cells = ""
        for b in bucket_names:
            s = d["buckets"][b]
            cells += f"{s['breaks']}/{s['replies']} ({s['break_rate']:.1%})".rjust(14) if s["break_rate"] is not None else "-".rjust(14)
        o = d["overall"]
        cells += f"{o['breaks']}/{o['replies']} ({o['break_rate']:.1%})".rjust(14)
        print(f"{name:<32}{cells}")
    print(f"\n{'ALL DISEASES':<32}", end="")
    for b in bucket_names:
        s = metrics["overall_buckets"][b]
        print(f"{s['breaks']}/{s['replies']} ({s['break_rate']:.1%})".rjust(14), end="")
    o = metrics["overall"]
    print(f"{o['breaks']}/{o['replies']} ({o['break_rate']:.1%})".rjust(14))
    print(f"\nBreak reasons: {metrics['break_reasons'] or 'none'}")
    if o["nulls"]:
        print(f"Failed calls recorded as null: {o['nulls']}")


async def main() -> None:
    records = _load_checkpoint()
    if records:
        if "--fresh" in sys.argv:
            records = []
        elif "--resume" not in sys.argv:
            answer = input("Partial run found. Resume from checkpoint? [y/n] ").strip().lower()
            if answer != "y":
                records = []
    records = await run_trials(records)
    metrics = compute_metrics(records)
    save_results(
        "consistency",
        {
            "persona": {"name": PATIENT_NAME, "age": PATIENT_AGE},
            "questions": INTERVIEW_QUESTIONS,
            "n_trials": N_TRIALS,
            "records": records,
            "metrics": metrics,
        },
    )
    CHECKPOINT_PATH.unlink(missing_ok=True)
    print_metrics(metrics)


if __name__ == "__main__":
    asyncio.run(main())
