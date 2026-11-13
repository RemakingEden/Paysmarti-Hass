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

## Review fixes

Home Assistant 2026.11 `Store.async_save` catches and logs `WriteError` without raising, and during Core shutdown it defers writing until a final-write event. `Store.async_load` can also return pending in-memory data. The journal now uses `atomic_writes=True` and reads the physical `.storage` JSON after every save, comparing the complete attempt record. A missing, malformed, or stale file raises `OSError`; therefore `begin` and `mark_commit_started` cannot authorize the final POST after a silent write failure or deferred write. A Store-like silent-write fake covers this path.

The journal now provides `mask_registration(full_registration)`, which normalizes a 1–10 character plate and masks at least one real character, and `begin` accepts only that prefix-mask shape. A `booked` outcome requires ordered, timezone-aware `starts_at` and `ends_at` values; the notification displays them.

Home Assistant persistent notifications are memory-only. Startup recreates the latest undismissed completed notification. A dispatcher listener records a dismissal tombstone in the same durable attempt record; startup leaves a dismissed completed notification hidden. The action owner should call `journal.close()` on config entry unload to remove the listener. Since only the latest attempt is retained by design, notifications from older attempts cannot be restored after Core restart.

Review-fix TDD evidence: new tests first failed for missing `mask_registration`, unverified silent writes, missing booked-time validation, and missing dismissal state. Final `.venv-ha/bin/pytest tests/test_outcome.py -q`: **19 passed**. Final `.venv-ha/bin/pytest -q`: **123 passed**. Both runs show one Home Assistant dependency deprecation warning.
