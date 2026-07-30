# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MealBot: a WhatsApp bot (via Twilio) that lets a user send a photo of a meal, uses Gemini to estimate its nutrition, and tracks daily calorie/protein progress against goals set during onboarding. Backend is a single FastAPI app backed by SQLite.

## Commands

Activate the venv first (`.venv` already exists):

```bash
source .venv/bin/activate
```

Run the dev server (webhook lives at `POST /whatsapp`, health check at `GET /health`):

```bash
uvicorn main:app --reload
```

Run all tests:

```bash
pytest
```

Run a single test file or test:

```bash
pytest test_decision_policy.py
pytest test_decision_policy.py::test_name -v
```

Manually exercise the Gemini nutrition analysis against a saved image in `uploads/` (edit the path in the script first):

```bash
python run_nutrition_manual.py
```

Required environment variables (see `.env`, not committed): `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (used by reminders), `GEMINI_API_KEY`. Optional: `PUBLIC_BASE_URL` (the app's public HTTPS origin, used to reconstruct the exact URL Twilio signed when validating webhook requests — without it, signature validation falls back to the request's own URL, which is wrong behind most proxies) and `TWILIO_VALIDATE_SIGNATURE` (defaults to `"true"`; set to `"false"` for local dev where there's no real Twilio-signed request, e.g. plain curl).

## Architecture

Request flow for `POST /whatsapp` in [main.py](main.py):

1. The request's `X-Twilio-Signature` header is validated against the full form body and the canonical webhook URL (`validate_twilio_signature`); requests that fail are rejected with 403 before touching the database. Validation can be disabled locally via `TWILIO_VALIDATE_SIGNATURE=false`.
2. Every inbound `MessageSid` is checked against `InboundMessage` first. If it's already been processed (a Twilio retry), the previously computed reply is returned immediately with no reprocessing — this is what prevents a redelivered text message from double-advancing onboarding or otherwise re-mutating state.
3. Phone number is normalized ([conversation.py](conversation.py)) and the `User` row is fetched or created.
4. If the user hasn't finished onboarding (`onboarding_state != ACTIVE`), **or** the message body is one of the control commands `"start"` / `"reset"` / `"goals"`, the message is routed to `handle_message` in [conversation.py](conversation.py), a pure state machine (no FastAPI/Twilio/DB calls) that walks `new → awaiting_calorie_goal → awaiting_protein_goal → active` and mutates the passed-in `User` in place. `"reset"` returns to `new` from any state (including `active` — routing it through even when active is what makes that work); `"goals"` re-enters goal collection from `active` without a full reset, and gets different copy ("Let's update your goals...") than a first-time `"start"`. `"help"` inside `handle_message` (i.e. anywhere before `active`) explains the product and points at `"start"`.
5. Once active, an incoming image is downloaded via [storage.py](storage.py) (`download_and_save_image`), which authenticates to Twilio's media URL, enforces an allow-list of content types and a 10 MB cap, and saves to `uploads/` under a filename derived from a SHA-256 hash of the Twilio `MessageSid` (so redelivery is idempotent). A `Meal` row is created with `status="stored"`, and the real analysis is deferred to a FastAPI `BackgroundTask` so the Twilio webhook can return its TwiML reply immediately.
6. If active and the message has no image, it's checked against a small set of text commands before falling back to the clarification-resume flow: `"help"`/`"menu"` (`main.HELP_TEXT`, the full command list), `"today"`/`"progress"` (`main.build_today_summary` — lists today's logged meals plus the same progress line as step 9), `"undo"` (`main.undo_last_meal` — deletes the `NutritionEstimate` for the most recent meal logged *today*, marks the `Meal` `"undone"`; the row stays for audit but drops out of every aggregation query), and `"reminders off"` / `"reminders on"` (toggles `User.reminders_enabled`). Anything else falls through to `main.get_pending_meal`, which looks for the user's most recent `Meal` still waiting on a clarification (`needs_new_image` / `awaiting_dish_choice` / `awaiting_serving_size`). If one exists, the reply text is stored on `Meal.clarification_text`, the meal is closed out as `clarification_closed`, and the user is asked to resend a photo — we don't fabricate a nutrition estimate from text alone, consistent with the "don't trust it, don't log it" policy below. Otherwise the user is told to send an image (and pointed at `"help"`).
7. The background task, `analyze_and_reply` in [main.py](main.py), opens its own DB session (background tasks run after the request-scoped session is closed), calls `analyze_meal_image` ([nutrition.py](nutrition.py)) with up to 3 attempts (short backoff) to absorb transient Gemini failures, sending the image to Gemini (`gemini-flash-latest`) and validating the JSON response against the `MealImageAnalysis` Pydantic schema.
8. The analysis is passed through `decide_meal_action` in [decision_policy.py](decision_policy.py), a pure policy function that decides whether to trust the model's output enough to auto-log it, or instead ask the user a clarifying question. It refuses to auto-log when: the image is unusable or has no visible foods, multiple dish candidates are plausible without high confidence, portion size confidence is low, or there's no estimate at all. Every attempt (successful or not) is recorded as a `MealAnalysisAttempt` row for auditability, independent of whether it resulted in a logged meal.
9. On auto-log, a `NutritionEstimate` row is persisted with min/max ranges for calories/protein/carbs/fat/fibre, and the reply includes today's cumulative progress against the user's goals (`get_daily_nutrition_totals` in [main.py](main.py), which sums estimates within the user's local calendar day using `Asia/Kolkata`, converting to naive UTC via `local_day_bounds_utc` to match how SQLite stores timestamps — also reused by `build_today_summary` and `undo_last_meal`). `format_daily_progress` expresses progress as a remaining amount (`format_remaining`, e.g. `"1,050–1,300 kcal left"`) rather than a percentage, since two overlapping percentage ranges was the least intuitive part of the old copy.
10. On any other decision, the `Meal.status` is set to a corresponding pending state (`needs_new_image`, `awaiting_dish_choice`, `awaiting_serving_size`) and the user is sent a clarifying question, resumed per step 6 above.
11. Every proactive (Twilio REST API) send — the meal analysis reply and reminders — goes through `send_with_audit` ([messaging.py](messaging.py)), which retries transient failures (up to 3 attempts, short backoff) and always records the outcome as an `OutboundMessage` row, so a failed send is visible in the database instead of only a log line.

Key design point: nutrition estimates are always stored as ranges (`NutritionRange`, min/max), not point values, and the model is explicitly instructed (see `NUTRITION_PROMPT` in [nutrition.py](nutrition.py)) to widen ranges under uncertainty and never give dieting/medical advice — the whole point of `decision_policy.py` is to keep untrustworthy model output out of the logged/aggregated data rather than to silently accept it.

Reminders ([reminders.py](reminders.py)) run as `asyncio` background tasks started in the FastAPI `lifespan` (see `main.py`), one per meal in `MEAL_REMINDERS`, each looping forever and sleeping until its next local wall-clock time before messaging every `User` with `onboarding_state == ACTIVE` and `reminders_enabled == True` (toggled via the `"reminders off"` / `"reminders on"` commands above). They're sent as freeform WhatsApp text via the Twilio REST API — this works on the Twilio sandbox, but proactive (business-initiated) messages to real WhatsApp numbers outside the 24-hour customer service session window require an approved WhatsApp Content Template; reminders will need to be switched to a `ContentSid` once templates are approved.

At startup, `recover_stuck_meals` in [main.py](main.py) sweeps any `Meal` still at `status="stored"` — these are meals whose analysis `BackgroundTask` never finished (e.g. the process restarted mid-analysis) and would otherwise sit silently unprocessed forever. Each is marked `analysis_failed` and the user is notified to resend the photo.

Data model ([models.py](models.py)): `User` (keyed by normalized phone, holds goals + onboarding state) → `Meal` (one per inbound image, keyed by Twilio `MessageSid` for idempotency) → `NutritionEstimate` (one-to-one, the accepted result) and `MealAnalysisAttempt` (one-to-many, every analysis/decision attempt including failures, for debugging the model and the policy). `InboundMessage` (keyed by `MessageSid`) and `OutboundMessage` form the message audit trail described above.

`database.py` uses a single SQLite file (`mealbot.db`) with `check_same_thread=False`; there are no migrations — schema changes are applied via `Base.metadata.create_all` on startup, so adding/changing columns on existing tables requires manually altering `mealbot.db` or dropping it in development.

## Eval harness

`eval/` is a local, manually-run harness for objectively comparing prompt or
model changes to `nutrition.py`, instead of eyeballing
`run_nutrition_manual.py` output. It's not wired into CI (see
`eval/run_eval.py`'s docstring for why: real Gemini calls cost money and are
non-deterministic).

- `eval/golden/cases.json` + `eval/golden/images/` — the golden dataset: one
  entry per fixture image with an `expected_decision` (`auto_log` /
  `ask_dish_choice` / `ask_serving_size` / `request_new_image` / `null`) and
  optional `expected_calories_kcal` / `expected_protein_g` ranges. `null`
  fields mean "tracked but not scored" — the two seeded cases are
  placeholders with no ground truth yet. To add a real case: drop an image
  in `eval/golden/images/`, add an entry to `cases.json` with your own
  verified expected ranges/decision.
- `eval/golden_dataset.py` — loads and validates the manifest
  (`load_cases()` → `list[GoldenCase]`).
- `eval/metrics.py` — scores each case (`score_case`) against the real
  pipeline's output. The single most important signal is `false_auto_log`:
  the model auto-logged a meal when it should have asked a clarifying
  question — the exact failure mode `decision_policy.py` exists to prevent.
  Range accuracy uses strict containment (your expected range must sit
  entirely inside the model's estimated range).
- `eval/run_eval.py` — the runner:
  ```bash
  python -m eval.run_eval                                   # run against the current prompt/model
  python -m eval.run_eval --save eval/results/baseline.json # save a named baseline
  python -m eval.run_eval --compare-to eval/results/baseline.json  # diff against it
  python -m eval.run_eval --model gemini-2.5-flash           # A/B a different model
  python -m eval.run_eval --prompt-file candidate_prompt.txt # A/B a different prompt
  ```
  Prints a per-case + aggregate report (decision accuracy, false-auto-log
  rate, latency, token counts — no automatic $ cost; fill in
  `PRICE_PER_1K_*_TOKENS` in `eval/metrics.py` with current pricing if you
  want that), saves JSON results, and exits non-zero if
  `MAX_FALSE_AUTO_LOG_RATE` / `MIN_DECISION_ACCURACY` (top of
  `eval/metrics.py`) are violated — ready to plug into CI later via that
  exit code.
- `nutrition.analyze_meal_image_with_usage` is what the runner calls instead
  of `analyze_meal_image` — same logic, but also returns latency and token
  usage (`AnalysisResult`). `analyze_meal_image` (used by the production
  webhook path) is a thin wrapper around it and is unaffected.
