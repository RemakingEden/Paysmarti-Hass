# Paysmarti visitor booking for Home Assistant

**Implementation update:** The user later asked for card dismissal to persist across refreshes and for system-wide booking notifications to be removed. The current implementation keeps the redacted attempt journal for recovery and diagnostics, shows the result in the card until dismissed, and stores dismissal per attempt. Earlier notification requirements below describe the original design decision.

Date: 13 November 2026  
Status: Draft for user review

## Intent

Book one visitor's car from a Home Assistant dashboard without manually repeating the Paysmarti login and booking forms. The booking starts immediately and lasts until 23:59 on the same Leeds calendar day. The user wants a dropdown of saved registrations and a text box for a new registration. A new registration may optionally be saved with a nickname.

## Goals, boundaries, and failure cases

- Support one LeedsCC account, one selected visitor permit, one fixed street, one car, and the site's visible `DAY` session.
- Show a dedicated booking card with saved registrations, a new registration field, a save checkbox, an optional nickname, the effective registration, a Book button, and a clear result.
- Refresh saved registrations from Paysmarti rather than baking captured IDs into code.
- Report success only after checking the site's success page. Show a distinct *outcome unknown* message if the final submission may have succeeded but cannot be verified. Keep the latest result visible after the card reloads, and create a Home Assistant persistent notification before each attempt proceeds to the final booking POST.
- Fail visibly and stop before the final POST when Paysmarti changes a required form, token, redirect, permit, street, or confirmation value. Give a non-sensitive diagnostic stage and a clear instruction to update or repair the integration.
- Keep credentials, cookies, anti-forgery tokens, raw pages, and registration numbers out of application logs and repository files.

Multi-day bookings, multiple cars in one submission, future start dates, multiple accounts, and other councils are outside this first version. A failure would include silently booking the wrong car, street, or date, or retrying an uncertain final submission and creating a duplicate.

## Evidence and constraints

The redacted [Caido flow note](../../research/paysmarti-booking-flow.md) records two successful browser bookings: one using a saved registration, and one entering a new registration without saving it. The new-registration path has an extra registration confirmation before Step Two. The Step Two summary and success page showed parking from the current time through 23:59. The save-with-nickname path and an independent scripted login remain unverified.

Home Assistant recommends a configuration flow for integrations that communicate with a service, integration actions for operations, and a dedicated Home Assistant managed HTTP session when cookies should remain isolated. Its custom cards can call backend actions over the Home Assistant WebSocket connection. [Config flow](https://contributors.home-assistant.io/core/integrations/config-flow/), [integration actions](https://contributors.home-assistant.io/core/integrations/actions/), [HTTP session](https://contributors.home-assistant.io/core/quality-scale/inject-websession/), [custom cards and WebSocket](https://contributors.home-assistant.io/frontend/extending/websocket-api/).

## Approaches

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Native Home Assistant helper entities plus an action | Less frontend code, but conditional controls are awkward and newly typed registrations can persist in entity state and Recorder. | Not chosen. |
| Dedicated card backed by a custom integration and direct HTTP form client | More frontend work, but one coherent form, clear outcomes, and no new-registration helper state. The form client must track a brittle third-party workflow. | Chosen by the user. |
| Browser automation service | Executes page JavaScript, but needs a browser runtime, persistent process, and more failure handling around Home Assistant. | Reserve as fallback if direct form login proves unworkable. |

An official supported API would be preferable if Paysmarti exposes one for this account; none has been established from the capture. The design uses only the observed browser flow and does not infer multi-day support from hidden fields.

## User experience

The card loads the current saved registrations when opened and offers Refresh. The user either chooses a saved registration or enters a new one. Nonempty new text, after trimming and normalizing spacing/case, takes precedence; the card makes that effective choice visible before Book. The Save checkbox appears only for a new registration; ticking it shows an optional nickname. Unticking it clears the nickname. The card requires an effective registration and validates the new registration's basic format before enabling Book.

Because the captured new-registration booking had Save off, saving is disabled by default. The integration options flow offers an explicit "Enable saving new registrations" switch for the user's controlled first test on their server. Until enabled, the card hides the Save checkbox and nickname, while new-registration booking without saving remains available. A successful user test can leave this option enabled; an unexpected response fails closed before the final POST where possible.

Book is an explicit commit action. The card immediately changes to a prominent "Booking in progress" state and disables repeat clicks. It then shows a persistent green **Booked** panel with the effective registration, verified start/end times, and booking reference when available, or a prominent red/amber **Not booked**/**Outcome unknown** panel with the reason and next action. The panel remains until the user dismisses it or starts another attempt, and has an accessible live announcement; the card must never reset to an idle appearance after a failed request. A failed request leaves the entered values available for correction. If the result is unknown after the final POST, it instructs the user to check Paysmarti before trying again and does not offer an automatic retry. The card keeps newly typed values only in browser memory while open, then clears them after a confirmed success. The saved list is refreshed after a confirmed save. No extra card confirmation dialog is planned: the effective registration is visible next to Book, and the observed site's intermediate dialog only repeats the registration.

Every attempt also creates a Home Assistant persistent notification, first showing **Booking in progress** and then updated under the same attempt-specific notification ID. It remains until dismissed. A success notification includes the booking time and masked registration; a known pre-commit failure says **Not booked: final submission was not sent**, names the failed stage, and gives a recovery action; an uncertain outcome tells the user to check Paysmarti before another attempt. On card reload, a read action retrieves the last result and displays it again. This makes the outcome visible even if the card was closed during the request. Persistent notifications are a Home Assistant feature that users dismiss themselves. [Persistent notifications](https://www.home-assistant.io/integrations/persistent_notification/).

The card's Book button and backend action remain usable without the card through Home Assistant's Actions interface. The card JavaScript lives **inside** `custom_components/paysmarti_visitor/` and is served by the integration at a stable static URL. The user adds that URL as a JavaScript module dashboard resource after installing the integration from one Marketplace repository. This avoids a second card repository or a manual file copy.

## Backend design

The integration's configuration flow accepts the Paysmarti email and password, checks that a fresh login and visitor-site handoff work, and discovers the account's visitor permit and booking street. Setup binds the single intended permit and street by their current identifiers and labels. If there are zero or multiple candidates, setup asks the user to choose or stops with a clear message; it never silently picks the first. The dashboard does not show a street selector. Reauthentication is offered if credentials stop working.

The backend owns a small asynchronous Paysmarti client with an isolated, Home Assistant managed cookie session. It navigates only the expected HTTPS Paysmarti hosts, follows the login and WS-Federation handoff, and parses each form's action and hidden fields afresh. Captured cookies, customer IDs, permit IDs, and anti-forgery tokens are never constants. The client uses bounded timeouts, rejects unrecognized redirects or pages, and does not disable TLS checks.

Two integration actions form the card's interface:

1. `paysmarti_visitor.list_saved_registrations`, targeting the configured account, returns current saved registration labels, opaque selection identifiers, and whether the account's Save option is enabled. It has no booking side effect.
2. `paysmarti_visitor.book_visitor`, targeting the configured account, accepts exactly one saved selection or new registration, plus `save` and optional `nickname` for a new registration. It returns confirmed registration, start/end times, and booking reference if the success page supplies one. Invalid inputs and remote failures raise translated Home Assistant action errors. An uncertain final outcome raises a distinct, actionable error.
3. `paysmarti_visitor.get_last_result`, targeting the configured account, returns the last attempt's status, timestamp, safe stage/error code, masked registration, and verified times when known. It has no booking side effect and lets a reopened card restore the result.

The card calls these actions through Home Assistant's authenticated WebSocket connection, requesting response data. The backend serializes bookings for the account so two card presses or automations cannot interleave one session's form state. The action rechecks the selected vehicle, configured street, permit, `DAY` type, immediate start, and same-day end in the Step Two summary before posting the confirmation. For a new registration, it recognizes the extra Step One registration prompt, checks that it names the intended registration, and submits its fresh form with `ConfirmModal=True` once. A repeated or changed prompt stops the flow.

The Step Two POST is the commit boundary. Before it, a failed request can be reported as not booked. After it, a timeout, broken redirect, or missing success evidence is *outcome unknown*: the client must not automatically resend. A 302 alone is not proof of success. The backend follows the redirect to the success page and checks the stated registration and parking times. A later booking-history lookup may improve reconciliation only if its reliability is verified.

At action start, the backend saves a minimal attempt record with a new opaque attempt ID, timestamp, masked registration, and `preparing` status, and creates its in-progress persistent notification. If either step fails, the action stops before the final POST. Immediately before sending that POST, it saves `commit_started` and waits for the save to finish. It updates the record and notification on success, known failure, or unknown outcome. If notification updating fails after submission, the stored result remains authoritative and the integration retries only the notification update; it never retries the booking POST. On Home Assistant restart, an attempt still marked `preparing` becomes a failed or interrupted pre-commit attempt; one marked `commit_started` becomes **outcome unknown** and updates its persistent notification. The card reads that state at startup. This closes the gap where Home Assistant stops after sending the booking request but before the card receives a response. Record only the latest attempt in integration storage; keep each notification until the user dismisses it.

Treat site changes as explicit failures, not generic timeouts: classify `authentication_failed`, `login_challenge`, `unexpected_redirect`, `form_changed`, `token_missing`, `vehicle_missing`, `street_changed`, `permit_changed`, `summary_mismatch`, and `success_unverifiable`. Before commit, these mean **Not booked**. If the change appears after the final POST, it means **Outcome unknown**. No parser fallback may silently choose a different vehicle, permit, street, day type, or start date. Errors from the action use Home Assistant's translated exception mechanism, while the card shows the stage and a practical next step. [Action errors](https://contributors.home-assistant.io/core/integrations/setup-failures/).

Keep each page parser in a small, named function with fixture coverage for the observed form shape. When a required shape changes, create a Home Assistant Repair issue explaining that the Paysmarti flow changed, and keep a persistent notification for the failed attempt. Offer a redacted diagnostics download containing integration version, failing stage, error code, HTTP status, response path without query, expected versus observed form actions and field names, and the last attempt status. Never include page bodies or field values. The README will explain how to update the integration and, if needed, capture a redacted new browser flow for a maintainer. Successful operation after an update clears the Repair issue, but does not dismiss the user's attempt notification. [Repairs](https://contributors.home-assistant.io/core/integrations/repairs/), [diagnostics](https://contributors.home-assistant.io/core/integrations/diagnostics/).

## Data and privacy

The password is held in Home Assistant's config entry data for this account. Session cookies and anti-forgery tokens stay in the isolated HTTP session and are not persisted by this integration. The card does not put a newly typed registration into a Home Assistant entity state. Saved registrations are returned only to an authenticated Home Assistant client requesting the list. The last-result store and persistent notifications contain a masked registration, timestamps, status, and non-sensitive stage code, but no password, cookie, token, full registration, raw page, or personal URL. Logging records the stage and error category only. The README will explain that anyone allowed to run the booking action in Home Assistant can create a real permit booking.

## Verification and rollout

First, build sanitized fixtures from the two captured flows and test the form state machine: login redirects, token changes, saved vehicle, new vehicle with its extra prompt, street and date mismatch, unexpected form, final response loss, and success-page parsing. Test each site-change class before and after the commit boundary. Then verify fresh login independently against Paysmarti and confirm session renewal and failed-login behavior. Controlled live checks must cover a new registration with Save on, nickname optionality, saved-list refresh, and any payment or voucher limit response before advertising those controls as working. The implementation should fail closed for a branch that has not passed its live check.

Test the Home Assistant config flow, actions, and card with a local Home Assistant instance or fixtures. Verify success, known failure, and unknown-outcome panels; persistent notifications; reload of the last result; repeat-click blocking; and recovery of a `commit_started` record after a simulated Home Assistant restart. Test a changed form creates a Repair issue and a useful redacted diagnostics file, then a compatible response clears the issue. Check that no sensitive values appear in logs, diagnostics, test output, or committed files. The first release is ready when both vehicle paths and the chosen save behavior are verified, the card shows confirmed or uncertain outcomes accurately, and an unexpected site change stops before the final POST.

## Choices made for the first version

- One account and one configured permit and street; setup handles ambiguity, while the booking card stays focused on one car.
- Direct HTTP form client with an isolated cookie session; browser automation is a contingency.
- Dedicated card with a visible effective registration and a single Book action; no second confirmation dialog.
- No automatic retry after the final POST and no multi-day request fields.
- A durable last-result record, persistent notification for every attempt, and an explicit unknown-outcome state make failed or interrupted bookings visible after the card closes or Home Assistant restarts.

The repository follows the Home Assistant Marketplace's custom-integration layout: one `custom_components/paysmarti_visitor/` directory, root `hacs.json`, manifest metadata, README, and a tagged version. The Marketplace installs only the integration directory, so the card asset must be inside it. A public GitHub URL is needed for the user's custom-repository installation; publishing and installation are separate final steps. [Marketplace repository structure](https://contributors.home-assistant.io/marketplace/reference/repository-structure/), [registering card resources](https://contributors.home-assistant.io/frontend/custom-ui/resources/).

The user will install and configure the integration on their separate Home Assistant server. Development and most verification use captured, sanitized fixtures locally. No further real booking is part of routine development. If one end-to-end booking is needed, the implementation must first be reviewable, and the user will decide whether to run that single test on the server. The save-with-nickname server path and independent login/renewal remain unverified until then; any unverified branch is identified clearly and cannot be claimed as tested.
