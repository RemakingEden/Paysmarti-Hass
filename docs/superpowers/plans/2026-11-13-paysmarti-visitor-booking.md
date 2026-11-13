# Paysmarti Visitor Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Home Assistant integration and dedicated dashboard card that books one LeedsCC visitor vehicle for the current day through Paysmarti, with durable and unmistakable outcomes.

**Architecture:** An asynchronous Python client follows the observed login and form sequence using fresh cookies and tokens. Home Assistant config flow and actions wrap that client; a dedicated JavaScript card calls the actions and displays the saved-vehicle list and booking result. A small persisted attempt journal and Home Assistant notifications preserve outcomes across card closure or Core restart.

**Tech Stack:** Python, Home Assistant Core integration APIs, aiohttp, Beautiful Soup 4.15.0, pytest, vanilla JavaScript custom card, Vitest with jsdom for card behavior.

**Spec:** [approved design](../specs/2026-11-13-paysmarti-visitor-booking-design.md); [redacted browser evidence](../../research/paysmarti-booking-flow.md).

## Global Constraints

- Target one LeedsCC account, one chosen visitor permit, one fixed street, one car, and `DAY` starting immediately and ending at 23:59 in `Europe/London`.
- Normalize new registrations to uppercase with spaces removed, then enforce the captured form's `^[0-9A-Z]{1,10}$` client-side shape; treat any stricter server rejection as a clear input error.
- No multi-day, multiple-car, future-start, or other-council request paths.
- Never embed captured cookies, IDs, anti-forgery tokens, or credentials. Keep full registrations, raw pages, and personal URLs out of logs, diagnostics, notifications, and persisted attempt state.
- Use a Home Assistant managed but isolated cookie session, verified HTTPS, bounded requests, and an allowlist of the observed Paysmarti hosts.
- An unexpected pre-commit response means **Not booked**; an uncertain response after the final Step Two POST means **Outcome unknown**. Never automatically resend that POST.
- Booking success requires the success page and matching registration and times. A redirect alone is insufficient.
- The repo is currently not a Git repository. Initialize Git as part of the first implementation task; do not expect existing source files.
- Development and fixture tests run on this laptop. The user's Home Assistant runs on a separate server; the user will install and configure the finished repository there.
- Package one Marketplace custom-integration repository with root `hacs.json`, README, and exactly one `custom_components/paysmarti_visitor/` folder. Keep the card JavaScript inside that folder, because Marketplace installs only integration contents.
- Make **zero** real bookings during normal implementation. After local work is reviewable, offer at most one user-run end-to-end booking on the server if still needed; never spend permits on iterative tests.

## Review Focus

These five input/failure classes must have tests in the owning tasks:

1. A new registration plus a selected saved registration uses the new value and displays the effective choice; malformed or empty registrations never reach Paysmarti (Tasks 4, 8).
2. Login expires, fails, or adds an interactive challenge; the client reauthenticates only before commit or stops with an actionable error (Tasks 2, 6).
3. Permit or fixed street becomes absent or ambiguous; no arbitrary first option is chosen (Task 3).
4. The final POST times out or its success page changes; it is marked unknown and never resent, including after Core restarts (Tasks 5, 7).
5. Notification creation fails or the card closes while a request runs; no final POST proceeds without the initial notification, and the latest outcome is visible after reload (Tasks 7, 9).

## File map

| File | Responsibility |
| --- | --- |
| `.gitignore`, `pyproject.toml`, `README.md`, `hacs.json`, `.github/workflows/validate.yml` | Repository, Python test setup, Marketplace metadata and checks, installation and troubleshooting. |
| `custom_components/paysmarti_visitor/manifest.json`, `const.py`, `models.py` | Integration metadata, domain and hosts, typed request/result contracts. |
| `custom_components/paysmarti_visitor/forms.py` | HTML form extraction and page-shape validation. |
| `custom_components/paysmarti_visitor/auth.py` | Login and WS-Federation handoff with isolated session. |
| `custom_components/paysmarti_visitor/discovery.py` | Current permit, fixed street, and saved registration discovery. |
| `custom_components/paysmarti_visitor/booking.py` | Step One, optional registration prompt, Step Two summary, final POST, success check. |
| `custom_components/paysmarti_visitor/__init__.py`, `config_flow.py` | Config entry setup, session lifecycle, setup and reauth; register the bundled card's static HTTP path. |
| `custom_components/paysmarti_visitor/outcome.py` | Minimal persisted attempt state and persistent notifications. |
| `custom_components/paysmarti_visitor/services.py`, `services.yaml`, `strings.json` | Three typed actions and user-facing translations. |
| `custom_components/paysmarti_visitor/issues.py`, `diagnostics.py` | Repair issue and redacted site-change diagnostics. |
| `custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js`, `package.json`, `tests_frontend/card.test.js` | Bundled dashboard card and behavior tests. |
| `tests/conftest.py`, `tests/test_forms.py`, `test_auth.py`, `test_discovery.py`, `test_booking.py`, `test_config_flow.py`, `test_outcome.py`, `test_services.py`, `test_diagnostics.py` | Python fixtures and focused tests. |
| `tests/fixtures/{login,permit,step_one_saved,step_one_new_prompt,step_two,success}.html` | Sanitized structural snapshots; no real tokens, registrations, or names. |

## Tasks

### Task 1: Repository and form contract

**Files:** `.gitignore`, `pyproject.toml`, `hacs.json`, `.github/workflows/validate.yml`, `custom_components/paysmarti_visitor/{manifest.json,const.py,models.py,forms.py}`, `tests/{conftest.py,test_forms.py,fixtures/*.html}`.

**Interfaces:** `parse_form(html: str, expected_action: str) -> FormSnapshot`; `FormSnapshot(action: str, fields: dict[str,str])`; `UnexpectedPage(stage, path, field_names)`; `BookingInput(saved_vehicle_id: str | None, new_registration: str | None, save: bool, nickname: str | None)`; `BookingReceipt(registration: str, starts_at: datetime, ends_at: datetime, reference: str | None)`; `VehicleOption`, `PermitOption`, `StreetOption` in `models.py`. `forms.py` uses Beautiful Soup's built-in `html.parser`; manifest pins `beautifulsoup4==4.15.0`.

- [ ] Initialize Git, add ignore rules for credentials, Python/Node caches, and local captures; create the Marketplace layout, root `hacs.json`, a validation workflow, and the integration/test skeleton.
- [ ] Write sanitized HTML fixtures with the observed form action names and hidden-field shapes; replace every personal or session value with a harmless fixture value.
- [ ] Write `test_forms.py` cases asserting `parse_form` finds the fresh anti-forgery field and rejects a missing token, wrong action, or duplicate required field.
- [ ] Run `pytest tests/test_forms.py -q`; expect failure because `parse_form` is absent.
- [ ] Implement the typed form extraction and shape errors, then rerun the same command; expect all cases to pass.
- [ ] Commit as `test: establish Paysmarti form fixtures and parser`.

### Task 2: Login and session recovery

**Files:** `custom_components/paysmarti_visitor/{auth.py,models.py}`, `tests/test_auth.py`.

**Interfaces:** `PaysmartiAuthenticator(session, allowed_hosts).login(email: str, password: str) -> None` and `.ensure_visitor_session() -> None`; emits `AuthenticationFailed`, `InteractiveChallenge`, `UnexpectedRedirect`. Takes an injected aiohttp-compatible session for fake-transport tests.

- [ ] Write queued-response tests for email/password login, account handoff, visitor WS-Federation handoff, and fresh visitor cookie; assert only allowed HTTPS hosts are followed.
- [ ] Add tests for expired login before booking, wrong password, CAPTCHA/MFA page, and redirect to an unrecognized host; assert no booking request follows a failure.
- [ ] Run `pytest tests/test_auth.py -q`; expect missing authenticator or failing behaviors.
- [ ] Implement login and session checks using parsed form actions and the injected isolated session; do not log form bodies or headers.
- [ ] Rerun `pytest tests/test_auth.py -q`; expect pass, then commit as `feat: authenticate Paysmarti visitor sessions`.

### Task 3: Discover permit, fixed street, and saved vehicles

**Files:** `custom_components/paysmarti_visitor/{discovery.py,forms.py,models.py}`, `tests/test_discovery.py`.

**Interfaces:** `discover_context(session, expected_permit: str | None, expected_street: str | None) -> BookingContext`; `list_saved_vehicles(session, context: BookingContext) -> list[VehicleOption]`. `BookingContext` carries current permit and street IDs/labels, never captured constants.

- [ ] Write tests against sanitized permit and Step One fixtures for one permit/street, no option, two options, a configured label that disappears, and an empty saved-vehicle list.
- [ ] Run `pytest tests/test_discovery.py -q`; expect failures.
- [ ] Implement discovery from current forms; first setup may bind a single option, but ambiguity must require explicit setup choice and booking must revalidate the stored labels.
- [ ] Rerun `pytest tests/test_discovery.py -q`; expect pass, then commit as `feat: discover permit street and saved vehicles`.

### Task 4: Build and verify the pre-commit booking path

**Files:** `custom_components/paysmarti_visitor/{booking.py,forms.py,models.py}`, `tests/test_booking.py`.

**Interfaces:** `PaysmartiBooker(session, context: BookingContext).prepare(request: BookingInput) -> PreparedBooking`; `PreparedBooking` holds the current Step Two form and verified effective vehicle, street, permit, start, end, and session type. No final POST occurs in `prepare`.

- [ ] Write tests that a saved selection posts `UseNewVrm=False`, and a new registration posts `UseNewVrm=True`; new text wins when both are supplied, `ab 12 cd` becomes `AB12CD`, and empty or over-10-character/non-alphanumeric text fails before HTTP.
- [ ] Write tests that `save=True` posts `SaveThisVrm=True` with the optional `VehicleKnownAs`, while `save=False` clears it; new-registration Step One can return the observed confirmation prompt, whose fresh token is used once with `ConfirmModal=True`; repeated or changed prompts fail.
- [ ] Write tests that `prepare` rejects changed vehicle, street, permit, non-`DAY` session, future start, or end other than 23:59 on the local day in the Step Two summary.
- [ ] Run `pytest tests/test_booking.py -k prepare -q`; expect failures.
- [ ] Implement `prepare` and the summary parsers, then rerun the same command; expect pass. Commit as `feat: prepare and validate visitor bookings`.

### Task 5: Commit once and classify the result

**Files:** `custom_components/paysmarti_visitor/{booking.py,models.py}`, `tests/test_booking.py`.

**Interfaces:** `PaysmartiBooker.commit(prepared: PreparedBooking) -> BookingReceipt`; emits `BookingRejected` for a clear pre-commit rejection and `OutcomeUnknown` once the final POST might have been sent. The caller writes `commit_started` before invoking it.

- [ ] Write tests that the final Step Two POST is sent exactly once, follows only an allowed success redirect, and returns a receipt only when the success page matches registration and start/end.
- [ ] Write timeout, broken redirect, changed success page, and duplicate-click tests asserting no second final POST and `OutcomeUnknown` after submission may have started.
- [ ] Run `pytest tests/test_booking.py -k commit -q`; expect failures.
- [ ] Implement `commit` with one-shot semantics and bounded timeouts; rerun the command and expect pass. Commit as `feat: confirm visitor bookings once`.

### Task 6: Home Assistant account setup

**Files:** `custom_components/paysmarti_visitor/{__init__.py,config_flow.py,manifest.json,strings.json}`, `tests/test_config_flow.py`.

**Interfaces:** `entry.runtime_data` exposes `authenticator`, `context`, `booker`, an `asyncio.Lock`, and an isolated Home Assistant managed aiohttp session. Config flow stores email/password and chosen permit/street; reauth updates credentials without losing the binding. Options flow stores `enable_save_new`, default `False`.

- [ ] Set up a local test environment matching the current Home Assistant Core API; record the version in `pyproject.toml` test dependencies. The server's exact version is checked at installation, not assumed here.
- [ ] Write config-flow tests for valid login, one permit/street, multiple choices, invalid credentials, interactive challenge, reauth, unload/reload, and Save disabled by default until the user enables `enable_save_new`.
- [ ] Run `pytest tests/test_config_flow.py -q`; expect failure, then implement the flow and runtime setup.
- [ ] Register the bundled card file at `/api/paysmarti_visitor/paysmarti-visitor-card.js` using Home Assistant's async static-path API, and test that setup exposes that path without touching the server's `www` directory.
- [ ] Rerun `pytest tests/test_config_flow.py -q`; expect pass. Commit as `feat: configure and renew Paysmarti account`.

### Task 7: Durable attempts and unmistakable notifications

**Files:** `custom_components/paysmarti_visitor/outcome.py`, `tests/test_outcome.py`.

**Interfaces:** `AttemptJournal.begin(masked_registration: str) -> Attempt`; `.mark_commit_started(attempt_id) -> None`; `.finish(attempt_id, status: Literal['booked','not_booked','outcome_unknown'], safe_details: dict) -> None`; `.last() -> Attempt | None`; `.recover_on_startup() -> Attempt | None`. Each state transition is saved before the next network step; the notification ID is derived from the opaque attempt ID.

- [ ] Write tests for in-progress notification creation, final success/error/unknown updates, and last-result reload; assert notification text contains no full registration or credential.
- [ ] Write tests that a failure to save or create the initial notification blocks the final POST, `preparing` recovers as interrupted before commit, and `commit_started` recovers as unknown without a resend.
- [ ] Run `pytest tests/test_outcome.py -q`; expect failures.
- [ ] Implement the Home Assistant Store adapter and persistent notification updates, then rerun the command; expect pass. Commit as `feat: retain booking outcomes across restarts`.

### Task 8: Actions, repair issue, and redacted diagnostics

**Files:** `custom_components/paysmarti_visitor/{services.py,services.yaml,strings.json,issues.py,diagnostics.py,__init__.py}`, `tests/{test_services.py,test_diagnostics.py}`.

**Interfaces:** actions `paysmarti_visitor.list_saved_registrations`, `paysmarti_visitor.book_visitor`, and `paysmarti_visitor.get_last_result`, all targeted by config entry ID. `list_saved_registrations` returns `{vehicles: list[VehicleOption], can_save_new: bool}`. `book_visitor` returns receipt data only on confirmed success and raises translated `ServiceValidationError` or `HomeAssistantError` otherwise.

- [ ] Write service tests for new-text precedence, invalid input, serialized calls, successful receipt, pre-commit failure, unknown post-commit result, Save rejected while `enable_save_new=False`, and refresh of saved registrations after confirmed save when enabled.
- [ ] Write diagnostics tests asserting only version, stage, code, HTTP status, path without query, and form field names are exported; passwords, tokens, full registration, and page bodies are absent. Assert a changed form creates a Repair issue and later compatible operation clears it.
- [ ] Run `pytest tests/test_services.py tests/test_diagnostics.py -q`; expect failures.
- [ ] Register translated actions during `async_setup`, connect them to the client and journal, and implement the Repair issue and diagnostics; rerun the command and expect pass.
- [ ] Commit as `feat: expose safe booking actions and diagnostics`.

### Task 9: Dedicated card and server installation guide

**Files:** `custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js`, `package.json`, `tests_frontend/card.test.js`, `README.md`, `hacs.json`.

**Interfaces:** card config contains the integration's config entry ID. It calls the three actions through Home Assistant WebSocket with response data and renders saved dropdown, new text, Save checkbox when `can_save_new` is true, conditional nickname, effective registration, Book, Refresh, and the restored last result.

- [ ] Write jsdom tests for saved/new precedence, save/nickname visibility and clearing, invalid input, loading lock, green confirmed result, red pre-commit failure, amber unknown result, and last-result restore after a simulated card reload.
- [ ] Run `npm test`; expect failure, then implement the card with accessible status announcements and no registration stored in HA entity state or browser storage.
- [ ] Rerun `npm test`; expect pass. Run a browser fixture or temporary local Home Assistant dashboard smoke check and verify that closing/reopening the card restores the last outcome.
- [ ] Write Marketplace custom-repository installation steps for the user's **server**, plus one dashboard resource registration at `/api/paysmarti_visitor/paysmarti-visitor-card.js`, config flow, and card placement. Include the public GitHub repository URL once supplied, action-permission warning, update instructions, and redacted diagnostic capture steps. No manual card file copy.
- [ ] Validate the `hacs.json` and manifest structure locally; after the repository is published, the GitHub workflow runs Marketplace/HACS validation. Keep publication and server installation for the user's handoff.
- [ ] Commit as `feat: add dedicated visitor booking card and setup guide`.

### Task 10: Controlled integration verification

**Files:** `tests/fixtures/*.html`, `tests/test_booking.py`, `README.md` (only if observed behavior requires changes).

**Interfaces:** No new public interface. The save checkbox remains disabled or clearly unavailable until its live branch is verified.

- [ ] Run `pytest tests -q` and `npm test`; expect all tests to pass. Run a secret scan over source, fixtures, diagnostics, and logs.
- [ ] Verify fresh scripted login and session renewal without a booking if account access is available, then re-run both local test suites and a local card smoke check.
- [ ] Prepare a one-booking user test checklist for the server: check its exact Core version, install from the Marketplace custom repository, exercise Save on with a real visitor registration if the user chooses, confirm nickname/list refresh, and record only redacted observations. Do not run this booking as part of automated tests.
- [ ] Keep any unverified save branch disabled and state that limit in the README. Do not claim server install or live booking success until the user performs and reports it.
- [ ] Commit any resulting fixture or documentation updates as `test: verify live Paysmarti behavior`; stop short of claiming support for an unverified path.

## Self-review checklist

- [x] Spec coverage: each goal, UI behavior, booking transition, failure state, privacy rule, and installation step maps to a task above.
- [x] Type consistency: `BookingInput`, `PreparedBooking`, `BookingReceipt`, `BookingContext`, and `AttemptJournal` names match across producers and consumers.
- [x] Review Focus: all five listed failure classes have explicit test steps.
- [x] Proportion: tasks specify interfaces and assertions; the implementation body remains for the engineer.
