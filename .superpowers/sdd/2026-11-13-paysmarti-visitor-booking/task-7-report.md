# Task 7 report

Implemented a durable, redacted attempt journal in `custom_components/paysmarti_visitor/outcome.py` and focused tests in `tests/test_outcome.py`.

The async `AttemptJournal` methods are `begin`, `mark_commit_started`, `finish`, `last`, and `recover_on_startup`. `begin` persists `preparing` before creating an in-progress persistent notification and only returns after both succeed. `mark_commit_started` awaits its Store save before returning. Final outcomes are persisted before notification updates. A pending notification flag supports startup retry of an unconfirmed update without recreating a notification the user dismissed. Startup converts `preparing` to `not_booked` and `commit_started` to `outcome_unknown`; no booking request is issued by the journal. Store keys are scoped to the config entry. Only a masked registration, timestamp, opaque ID, status, approved stage/error categories, and verified ISO times may be stored.

## TDD and verification

- First `.venv-ha/bin/pytest tests/test_outcome.py -q`: import error because `outcome.py` did not exist.
- Focused red cases caught missing notification pending state and recreation of a dismissed final notification; another caught acceptance of an arbitrary lower-case error code.
- Final `.venv-ha/bin/pytest tests/test_outcome.py -q`: **10 passed**, one Home Assistant dependency deprecation warning.
- Final `.venv-ha/bin/pytest -q`: **114 passed**, the same warning.
- `git diff --check`: clean.

## Task 8 handoff

The action must `await journal.begin(masked_registration)` before it may approach final submission, then `await journal.mark_commit_started(attempt.id)` immediately before `booker.commit(prepared)`. Either exception prevents the final POST. After a known pre-commit failure call `finish(..., "not_booked", ...)`; after commit starts, use `booked` or `outcome_unknown` only. `safe_details` accepts `stage`, `error_code`, `starts_at`, and `ends_at`, with stage/error values from the allowlists in `outcome.py`; unknown site error categories should be mapped to `form_changed` or another approved category. Call `recover_on_startup()` during setup. Notification update failure after a saved final outcome is logged and retried on startup; it never triggers a booking retry. Service wiring is Task 8's scope.

No live requests or bookings were made.
