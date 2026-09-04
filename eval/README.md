# Evaluation suite

Standalone experiments for the conference paper. They exercise only
`app/services/llm_gateway.py` (Gemini calls) and
`app/services/character_guardrail.py` (raw `check_character_break`, no
retry/fallback orchestration) — **no FastAPI app, DB, or Redis required**.
The 4 diseases are defined as plain dataclasses in `eval/common.py`, and the
persona is fixed (Alex, 28) so persona is never a variable.

All scripts read `GEMINI_API_KEY` from the backend `.env`, throttle to ~10
requests/minute (free tier allows 15), retry failed calls up to 3× with
backoff, and record unrecoverable failures as `null` rather than aborting.
Every run is saved to `eval/results/{name}_{timestamp}.json`, so results are
never lost. **Cost: $0 on the Gemini free tier.**

Run everything from the backend root.

## Experiment 1 — speech-style separation

```bash
uv run python eval/speech_style_eval.py
```

Sends the identical opening question 10× per disease (40 calls, **~5 min**),
then prints word-count stats, type-token ratio, and a ROUGE-L F1 matrix
(within-style overlap on the diagonal, across-style off-diagonal). Distinct
styles should show higher within- than across-style overlap, plus separated
length/TTR distributions.

## Experiment 2 — long-conversation character consistency

```bash
uv run python eval/consistency_eval.py            # prompts to resume if a partial run exists
uv run python eval/consistency_eval.py --resume   # resume without prompting
uv run python eval/consistency_eval.py --fresh    # discard any checkpoint
```

A scripted 40-question clinical interview, 5 trials per disease, full history
sent every call (matches production). 800 calls ≈ **80 minutes** at 10 RPM.
Progress is checkpointed after every turn to
`eval/results/consistency.checkpoint.json`; an interrupted run resumes exactly
where it stopped. Prints character-break rates per turn bucket (1–10 … 31–40)
per disease and overall.

## Figures

```bash
uv run python eval/plot_results.py
```

Reads the **latest** results JSON of each experiment and writes 300 DPI PNGs
to `eval/results/` (no baked-in titles — LaTeX captions supply them):

- `fig_speech_length.png` — box plot of words-per-reply by speech style.
  Shows whether styles produce visibly different reply lengths.
- `fig_rouge_matrix.png` — heatmap of the ROUGE-L F1 matrix. A darker
  diagonal than off-diagonal means replies within a style resemble each
  other more than replies across styles.
- `fig_consistency.png` — cumulative character-break rate (%) vs. interview
  turn, one line per disease. A flat line near zero means the patient stayed
  in character for the whole interview.
