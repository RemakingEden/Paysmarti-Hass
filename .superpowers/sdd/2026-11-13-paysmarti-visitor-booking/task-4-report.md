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

Initial concern: the first sanitized Step Two fixture lacked permit and street summary detail. That fixture and parser were corrected against the captured response structure in the review below. Saving with a nickname remains unverified against the server.

## Review correction

Read-only inspection of Caido responses for requests 89, 185, and 186 showed the actual captured shape. Step Two uses a labeled `Permit` row with a `form-control-static` value and a sentence beginning `The total charge for parking`, followed by the registration, street, start and end times, `today`, and a charge span. It does not contain the earlier fixture's literal `Registration:`, `Street:`, `Session:`, or dated `Parking:` paragraphs. The revised parser and sanitized fixture use that observed shape. In-memory shape checks matched both captured Step Two responses and recognized the captured prompt; no raw response values were printed or stored.

The request 185 prompt includes other site scripts and a `bootbox.dialog` inside a wrapper. The parser now recognizes one exact registration-specific message within that script. It checks that the refreshed form retains vehicle choice, street, `StartParkingNow`, `DAY`, save choice, and nickname before sending `ConfirmModal=True` with its fresh token. A missing or changed field stops preparation. A new save-and-nickname prompt test covers these fields. The past-start allowance was reduced from 15 to 5 minutes; any future start is rejected.

Separate discovery mismatch for the parent task: captured permit responses to requests 64 and 183 contain hidden `SelectedPermit`, not a `PermitId` select. Captured Step One responses to requests 87, 184, and 185 contain hidden `StreetSelected`, not a street select. Task 3 discovery must reconcile those shapes before live use. The exact relationship between the discovered permit label and Step Two's permit value also remains to be verified. No live request or booking was made.

Review verification: `python -m pytest tests/test_booking.py -k prepare -q` — 27 passed; `python -m pytest -q` — 69 passed; `git diff --check` — clean.
