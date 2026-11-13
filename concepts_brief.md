# LeedsCC visitor booking: concept brief

## Goal and scope

Let a Home Assistant user book one LeedsCC visitor parking day from the dashboard without repeating the Paysmarti login and form flow. The booking starts immediately and should end at 23:59 on the same local calendar day. A booking action returns confirmed details, a specific rejection, or an explicit **outcome unknown** when the final submission may have succeeded but cannot be verified.

The first version targets one account, one eligible visitor permit, one vehicle per booking, and the observed `DAY` session. Multi-day, multiple-vehicle, scheduled, and multi-provider booking are outside scope. The repository currently contains a redacted research note, not an existing integration.

## Evidence and proposed behavior

- Two captured browser bookings reached a success page: one with a saved registration and one with a new registration and `SaveThisVrm=false`. The flows passed through account and visitor-site WS-Federation sessions and fresh anti-forgery forms. The Step 2 summary showed parking from the current time to 23:59. A future date in Step 1 did **not** determine the booked date.
- The new-registration Step 1 submit returned an intermediate JavaScript confirmation asking whether to book parking for the entered registration. Clicking Yes set `ConfirmModal=True` and resubmitted Step 1 with a fresh token, advancing to Step 2. The prompt did not present further terms in this capture.
- The dashboard offers a saved-registration dropdown and new-registration text. After trimming, nonempty new text takes precedence. Show the effective registration before booking; reject missing or invalid input. Show the save checkbox only for a new registration, and show an optional nickname only while save is checked. Clear or ignore hidden values.
- The user always books the same street. The dashboard needs no location selector. The client must identify the expected street during setup or permit discovery, match exactly one current form option, and verify it in the Step 2 summary. If it is absent, ambiguous, or changed, stop before booking.
- The integration owns credentials, sessions, form navigation, validation, and the booking action. The dashboard collects inputs and presents the result. The saved-registration list needs a defined source and refresh rule, including after a save and when a selected entry disappears.
- Before the state-changing Step 2 POST, parse the confirmation summary and verify the effective vehicle, eligible permit, location, immediate start, and same-day 23:59 end in the permit's local time zone. Stop on a mismatch. Follow the final redirect and require explicit success-page evidence; a 302 alone is insufficient.
- Serialize booking actions per account or permit. Never automatically retry the final POST. If its response or success-page check is lost, report `outcome_unknown` and direct the user to check Paysmarti before another attempt. An account readback could improve reconciliation only after its reliability is verified.

## Architecture choice

A small Paysmarti client should model account login, identity-provider handoff, visitor session, permit and vehicle discovery, Step 1 form, optional intermediate Step 1 confirmation, Step 2 summary, Step 2 commit, and success verification as separate states. Identify each returned page by its form and action, not by HTTP status alone. For the intermediate state, inspect its meaning and intended vehicle, street, day, and start choice; parse its action, hidden fields, and fresh token, then submit `ConfirmModal=True` at most once. An unexpected or repeated intermediate page stops the flow. Whether Step 1 has side effects, especially when saving, still needs verification. Parse all forms afresh; reject unexpected pages, missing tokens, and unrecognized redirect hosts. Keep this client separate from the Home Assistant config flow, action, and dashboard. Use asynchronous network I/O, bounded timeouts, and privacy-safe stage diagnostics. Do not store captured identifiers or tokens, or log credentials, registration numbers, cookies, raw HTML, or personal URLs.

The author chose a dedicated Home Assistant booking card instead of a dashboard assembled from persistent helper entities. The card will hold the new registration, save choice, and nickname in browser memory while the form is open, then call the integration's booking action. The backend will supply the current saved-registration list and return confirmed booking details. This choice adds frontend code but allows conditional controls and avoids retaining newly typed registrations as Home Assistant entity states.

| Option | Assessment |
| --- | --- |
| Direct form client | Provisional fit for a small HA integration; depends on brittle HTML and WS-Federation parsing. |
| Browser automation | May handle page JavaScript and sign-in changes, but adds a heavy runtime and session lifecycle to HA. Consider if direct login cannot be made reliable. |
| Manual portal handoff | Useful fallback for unsupported authentication or uncertain outcomes; does not provide the requested booking action. |
| Official API | Prefer if one is found and authorized; its existence is unknown. |

## Verification gates before implementation is committed

1. Verify fresh scripted login and renewal across all three sites, including expired sessions, failed credentials, and any MFA or CAPTCHA challenge. Interactive challenges must fail with a clear user action.
2. Reproduce the observed new-registration, save-off flow from a fresh client session, including the registration-specific Step 1 confirmation. Then exercise saving with and without a nickname. Confirm server validation, whether the nickname is truly optional, whether saving persists, and what happens if booking or saving partly succeeds.
3. Verify zero or multiple permits, saved-list retrieval and refresh, fixed-street identity and matching, booking limits, and duplicate-booking responses. Do not silently choose the first permit or street, or reuse a captured street identifier.
4. Test a lost response after the final POST and determine whether a reliable booking-history lookup exists. Until then, retain the unknown-outcome state and manual reconciliation.
5. Use sanitized fixtures to test redirects; saved-vehicle direct Step 1; new-registration intermediate Step 1 with a fresh token; changed or repeated intermediate forms; saving variants; fixed-street absence or ambiguity; conflicting inputs; summary mismatch; rejection; lost final response; and explicit success evidence. Define the HA action fields and result states before building dashboard controls.

## Main risks and decisions

The central conflict is one-click convenience versus uncertainty after a state-changing POST. A third result state preserves honesty and avoids an unexamined duplicate. New registration without saving is now observed; saving and nickname persistence remain unverified. The intermediate Step 1 prompt confirms the registration, so decide whether the Home Assistant Book action itself supplies that confirmation or whether a separate user confirmation is needed. Also decide where the expected fixed-street identity comes from, how to source and refresh saved registrations, how to handle multiple permits, and what evidence proves a saved registration persisted. Keep errors specific to authentication, changed form, invalid vehicle, unavailable permit, booking conflict, confirmation mismatch, network failure, and unknown outcome. Return booking reference and confirmed times when available.

## Goldfish reviewer status

| Lens | Original review | New-evidence follow-up |
| --- | --- | --- |
| `design_critic` | completed | completed |
| `codebase_details` | completed | not requested |
| `zoom_out` | completed | not requested |
| `comprehension` | completed | not requested |
| `implementation_readiness` | completed | completed |
| `structural` | completed | not requested |
| `inventive_catalyst` | completed | not requested |

All seven original reviewers read their persona, the earlier redacted flow note, and assigned bundled standards. The design critic and implementation readiness reviewers then read the updated note and this brief in fresh-context follow-ups. The other five did not review the new capture. No live Paysmarti requests or implementation changes were made.
