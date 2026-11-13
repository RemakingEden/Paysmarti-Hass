# Task 9 report — card and server installation guide

## Files

- Added `custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js`: vanilla JS custom card, authenticated WebSocket response actions, saved/new precedence, conditional Save and nickname, validation, lock, accessible result panels, journal restore.
- Updated `custom_components/paysmarti_visitor/__init__.py`: static card path `/api/paysmarti_visitor/paysmarti-visitor-card.js` via Home Assistant's async static path API, with cache headers disabled for updates.
- Added `package.json`, `package-lock.json`, `tests_frontend/card.test.js`: jsdom fixture and nine card behavior tests including close/reopen restoration and stale-result protection.
- Added `tests/test_frontend_resource.py`; updated `tests/test_services.py` test harness to include Home Assistant HTTP registration.
- Added `README.md`: server Marketplace custom-repository setup, one dashboard module resource, config flow and card placement, action permissions, updates and redacted diagnostics.
- Existing `hacs.json` and `manifest.json` were validated. The manifest documentation URL and README both retain explicit `OWNER` placeholders because the public GitHub owner is not yet known.

## Verification

- Red phase: `npm test` failed for missing card behavior; `pytest tests/test_frontend_resource.py -q` failed for absent static registration. A later regression test caught replay of an older green result after a rejected booking.
- `node --check custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js`: passed.
- `git diff --check`: passed.
- `npm test`: 9 passed, 0 failed. The jsdom fixture removed and recreated the card and observed the backend `get_last_result` action restore the booked panel on both opens.
- `.venv-ha/bin/pytest tests/test_frontend_resource.py -q`: 1 passed.
- `.venv-ha/bin/pytest -q`: 148 passed, 1 upstream aiohttp deprecation warning.
- Local JSON parse and metadata assertions for `hacs.json` and `manifest.json`: passed.

## Handoff and limits

- Replace the `OWNER` placeholder with the published public GitHub owner before Marketplace custom-repository installation. A tagged public repository is needed for the user's server installation; neither publication nor server installation was performed here.
- No live Paysmarti booking was made. The browser smoke check was a jsdom dashboard fixture rather than a running Home Assistant dashboard.
- If a failed action has no new journal result, the card shows amber uncertainty instead of treating an earlier success as the new attempt. The user should check Paysmarti before retrying.

## Follow-up review fix

A review found two card races. Added tests that first failed for each: a successful booking A followed by a rejected booking B replayed A's green result, and a delayed initial `get_last_result` allowed Book before restoration completed. The card now snapshots `get_last_result` immediately before every booking, compares the post-error journal result with that baseline, and shows amber uncertainty when the journal has not changed or cannot prove a new attempt. Initial restore keeps Book disabled until both startup reads finish.

A third failing regression covered switching `config_entry_id` while the old saved-list request is pending. The card now clears entry-specific form state and ignores late responses from the previous entry, including pending booking responses.

Fresh verification after the fix: `node --check custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js` passed; `git diff --check` passed; `npm test` passed 12/12; `.venv-ha/bin/pytest tests/test_frontend_resource.py -q` passed 1/1; `.venv-ha/bin/pytest -q` passed 148/148 with one upstream aiohttp deprecation warning. No live requests or bookings were made.
