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

Required environment variables (see `.env`, not committed): `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (used by reminders), `GEMINI_API_KEY`.

## Architecture

Request flow for `POST /whatsapp` in [main.py](main.py):

1. Phone number is normalized ([conversation.py](conversation.py)) and the `User` row is fetched or created.
2. If the user hasn't finished onboarding (`onboarding_state != ACTIVE`), the message is routed to `handle_message` in [conversation.py](conversation.py), a pure state machine (no FastAPI/Twilio/DB calls) that walks `new → awaiting_calorie_goal → awaiting_protein_goal → active` and mutates the passed-in `User` in place. Sending `"reset"` at any point returns to `new`.
3. Once active, an incoming image is downloaded via [storage.py](storage.py) (`download_and_save_image`), which authenticates to Twilio's media URL, enforces an allow-list of content types and a 10 MB cap, and saves to `uploads/` under a filename derived from a SHA-256 hash of the Twilio `MessageSid` (so redelivery is idempotent). A `Meal` row is created with `status="stored"`, and the real analysis is deferred to a FastAPI `BackgroundTask` so the Twilio webhook can return its TwiML reply immediately.
4. The background task, `analyze_and_reply` in [main.py](main.py), opens its own DB session (background tasks run after the request-scoped session is closed), calls `analyze_meal_image` ([nutrition.py](nutrition.py)) which sends the image to Gemini (`gemini-flash-latest`) and validates the JSON response against the `MealImageAnalysis` Pydantic schema.
5. The analysis is passed through `decide_meal_action` in [decision_policy.py](decision_policy.py), a pure policy function that decides whether to trust the model's output enough to auto-log it, or instead ask the user a clarifying question. It refuses to auto-log when: the image is unusable or has no visible foods, multiple dish candidates are plausible without high confidence, portion size confidence is low, or there's no estimate at all. Every attempt (successful or not) is recorded as a `MealAnalysisAttempt` row for auditability, independent of whether it resulted in a logged meal.
6. On auto-log, a `NutritionEstimate` row is persisted with min/max ranges for calories/protein/carbs/fat/fibre, and the reply includes today's cumulative progress against the user's goals (`get_daily_nutrition_totals` in [main.py](main.py), which sums estimates within the user's local calendar day using `Asia/Kolkata`, converting to naive UTC to match how SQLite stores timestamps).
7. On any other decision, the `Meal.status` is set to a corresponding pending state (`needs_new_image`, `awaiting_dish_choice`, `awaiting_serving_size`) and the user is sent a clarifying question — there's currently no handler that resumes a meal from these pending states.

Key design point: nutrition estimates are always stored as ranges (`NutritionRange`, min/max), not point values, and the model is explicitly instructed (see `NUTRITION_PROMPT` in [nutrition.py](nutrition.py)) to widen ranges under uncertainty and never give dieting/medical advice — the whole point of `decision_policy.py` is to keep untrustworthy model output out of the logged/aggregated data rather than to silently accept it.

Reminders ([reminders.py](reminders.py)) run as `asyncio` background tasks started in the FastAPI `lifespan` (see `main.py`), one per meal in `MEAL_REMINDERS`, each looping forever and sleeping until its next local wall-clock time before messaging every `User` with `onboarding_state == ACTIVE`.

Data model ([models.py](models.py)): `User` (keyed by normalized phone, holds goals + onboarding state) → `Meal` (one per inbound image, keyed by Twilio `MessageSid` for idempotency) → `NutritionEstimate` (one-to-one, the accepted result) and `MealAnalysisAttempt` (one-to-many, every analysis/decision attempt including failures, for debugging the model and the policy).

`database.py` uses a single SQLite file (`mealbot.db`) with `check_same_thread=False`; there are no migrations — schema changes are applied via `Base.metadata.create_all` on startup, so adding/changing columns on existing tables requires manually altering `mealbot.db` or dropping it in development.
