# Task 4 report

Implemented the pre-commit booking path. `prepare` selects the current permit and street, normalizes and validates the effective registration, posts Step One, handles one recognized new-registration prompt with its fresh token, and verifies the Step Two summary before returning its current form. It never posts Step Two.

Files changed: `custom_components/paysmarti_visitor/booking.py`, `models.py`, `tests/test_booking.py`, and `tests/fixtures/step_two.html`.

Verification:

- Initial `pytest tests/test_booking.py -k prepare -q`: import failed because the shell's `pytest` executable omitted the repository root from `sys.path`.
- Initial `PYTHONPATH=. pytest tests/test_booking.py -k prepare -q`: import failed because `booking.py` did not exist yet (expected red state).
- A focused future-start test failed before the strict time check was added (expected red state).
- `python -m pytest tests/test_booking.py -k prepare -q`: 23 passed.
- `PYTHONPATH=. pytest -q`: 65 passed.
- `git diff --check`: clean.

Concern: the sanitized Step Two fixture previously lacked permit, street, and session summary labels. It now models those as explicit paragraphs for strict verification. The exact live page shape remains unverified; a different shape will fail before the final POST. No live request or booking was made. Saving with a nickname remains unverified against the server.
