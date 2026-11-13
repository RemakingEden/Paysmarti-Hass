# Paysmarti visitor booking capture

Observed in the user's Caido history on 13 November 2026. These are two successful browser flows, inspected without replaying any request. Request IDs refer to the local Caido instance. Credentials, cookies, verification tokens, vehicle registration, customer identifiers, permit identifiers, and payment identifiers are omitted.

## Sequence

| Caido ID | Request | Observed result |
| --- | --- | --- |
| 1 | `POST permits.paysmarti.co.uk/login/LeedsCC/Home` with `email`, `password`, `returnUrl` | 302 to account site |
| 3–4 | Identity provider's WS-Federation form POST to account site | Account session established |
| 60–62 | Visitor site redirects through the identity provider; WS-Federation form POST to visitor site | Visitor session established |
| 64 | `GET /LeedsCC/BookParking/SelectPermit` | Permit selection form, with hidden permit and customer identifiers and a fresh verification token |
| 87 | `POST /LeedsCC/BookParking/StepOne` with the permit selection form | Step 1 form: vehicle, location, duration |
| 89 | `POST /LeedsCC/BookParking/StepOne` with vehicle, street, session type, start controls, and verification token | Step 2 confirmation form |
| 90 | `POST /LeedsCC/BookParking/StepTwo` with a new verification token and hidden fields from Step 2 | 302 to success URL |
| 91 | `GET /LeedsCC/BookParkingSuccess` | Step 3 success page showing permit and parking session details |

### New-registration booking captured later

| Caido ID | Request | Observed result |
| --- | --- | --- |
| 183–184 | Select permit and enter Step 1 | New-registration control available |
| 185 | Submit Step 1 with `UseNewVrm=True`, `SaveThisVrm=false`, one vehicle, `DAY`, start now | Step 1 returned with a confirmation state |
| 186 | Resubmit Step 1 with `ConfirmModal=True` and a fresh verification token | Step 2 confirmation page |
| 187–188 | Submit Step 2 and follow redirect | Success page for the new registration |

## Implementation implications

- The first booking used a saved vehicle (`VrmSelected`). The second booking used a new vehicle (`Vrm`, `UseNewVrm=True`) with saving off. Both used the same selected street (`StreetSelected`).
- The new-vehicle form has a `SaveThisVrm` checkbox and a `VehicleKnownAs` text field. The captured HTML does not mark `VehicleKnownAs` as required; server behavior when saving a new registration remains unverified.
- The captured new-registration input applies a client-side `^[0-9A-Z]*$` pattern and a maximum length of 10 after the page's input filter; server validation may be stricter.
- Inline JavaScript on the captured Step 1 page switches between saved and new registration controls using radio buttons. It sets `NewVrmRequired` or `SelectedVrmRequired` accordingly. It shows the name field when `SaveThisVrm` is checked and hides and clears it when unchecked. The same script switches the single-vehicle and multiple-vehicle controls.
- Step 1 posted `VisitorSessionType=DAY`. The semantics and availability of other session types are unverified.
- In this successful capture, the confirmation page reported parking allowed from the current time until 23:59 on the same calendar day. This is the observed meaning of a day booking for this permit; verify against the Step 2 summary on each booking.
- Step 1 posted a future date while `StartParkingNow=True`; the Step 2 confirmation used the current date and time instead. The integration must inspect the confirmation and must not infer the booked date from Step 1 input alone.
- The confirmation POST is the state-changing booking operation. A 302 alone should be followed to the success page and checked for a booking reference or other explicit success evidence.
- Both captured final POSTs redirect to `/LeedsCC/BookParkingSuccess` with `permit` and `paymentId` query parameters. The `permit` value matches the selected permit. The success page confirms the booking in a paragraph and presents the permit, registration, location, and parking start/end in labelled rows. No separate reference field is present in these captures.
- Authentication involves the account site, identity provider, and visitor site. Session cookies and anti-forgery tokens must be acquired per session; static values from the capture cannot be embedded in the integration.
- A fresh login must first visit the account site's `/acct/LeedsCC/` entry point, complete its federation handoff, and only then visit the visitor permit page. Starting from the visitor page with no account session can leave the later identity handoff returning HTTP 500. The credential POST contains `email`, `password`, and an empty `returnUrl` field.
- The site's `FedAuth` cookie contains characters that aiohttp quotes by default when it sends the Cookie header. Browser traffic sends this cookie unquoted; the quoted form reproducibly caused HTTP 500 at the identity handoff. An aiohttp session with `CookieJar(quote_cookie=False)` completed account login and permit/street discovery locally using the captured credentials. No booking was submitted during this verification.
- The initial Step 1 submit on the new-registration flow returned Step 1 with a JavaScript confirmation dialog asking whether to book parking for the entered registration. Clicking Yes sets `ConfirmModal=True` and resubmits Step 1; that advanced to Step 2. The client must handle this intermediate confirmation state rather than assuming Step 1 always advances immediately.
- The two captures do not establish login failures, multi-factor authentication, session expiry behavior, duplicate-booking behavior, permit selection when multiple permits exist, booking limits, payment, or the save-new-registration branch. These need focused verification before implementation decisions are finalized.

## Home Assistant direction

Home Assistant's integration action model fits a `book_visitor` operation. The dashboard can provide a saved-registration dropdown, a new-registration text input, and a booking button. The integration should use a config flow for account setup, and the booking action should return actionable errors and avoid logging personal or session data.

## Product decisions from discussion

- The dropdown selects an existing saved registration. A text box accepts a new registration and takes precedence when populated.
- Offer a save-registration checkbox for a new registration. Show an optional name field only when save is selected.
- Book one day starting immediately. Do not expose multi-day booking through hidden request fields; it is outside the visible browser flow and outside the initial scope.
- Assume one visitor permit and book one vehicle per action. Multiple-vehicle booking is outside the initial scope.
- The user reports that the booking location is always the same street. The integration can discover and validate that street during the form flow without showing a location control in the dashboard.
