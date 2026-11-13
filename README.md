# Paysmarti Visitor for Home Assistant

Licensed under the [MIT License](LICENSE).

Book a Paysmarti visitor parking session from a dedicated Home Assistant dashboard card. The integration supports saved registrations and new registrations, with optional saving when enabled in its options. It shows a confirmed receipt, a known failure, or an uncertain outcome. An uncertain outcome requires checking Paysmarti before another booking.

This integration follows the observed Paysmarti browser flow. Site changes may interrupt bookings. Use it with an account and visitor permit you control.

## Install on your Home Assistant server

**Public repository URL:** `https://github.com/RemakingEden/Paysmarti-Hass`. The repository must be public for Marketplace custom-repository installation. A published GitHub release is recommended for clear versioned updates; without one, Marketplace uses the latest default-branch commit. There is no separate card download or file copy.

1. On your Home Assistant **server**, install and open Home Assistant Marketplace (formerly HACS). In **Custom repositories**, add the published repository URL above as an **Integration** repository.
2. Find **Paysmarti Visitor** in Marketplace, download it, and restart Home Assistant. The integration package includes the dashboard card JavaScript.
3. Go to **Settings → Devices & services → Add integration**, search for **Paysmarti Visitor**, and complete the config flow with your Paysmarti account. Select the intended permit and street when prompted. Copy the integration's **config entry ID** from the entry URL/details for the card configuration.
4. Add a dashboard resource in **Settings → Dashboards → Resources** (enable advanced mode if Resources is hidden): URL `/api/paysmarti_visitor/paysmarti-visitor-card.js`, type **JavaScript module**. Register it once for the dashboard, then reload the browser page.
5. Edit the desired dashboard and add a **Manual** card:

   ```yaml
   type: custom:paysmarti-visitor-card
   config_entry_id: YOUR_CONFIG_ENTRY_ID
   ```

The card loads saved registrations when opened. Entering nonempty new text overrides the saved dropdown; the effective registration appears above **Book**. The Save checkbox appears only when a new registration is entered and **Enable saving new registrations** is turned on in integration options. Saving is off by default. **The Save with nickname path has not been verified against the live Paysmarti site.** Enable it only for the controlled one-booking test below; the card labels it unverified. Check the effective registration before selecting **Book**.

## One-booking server verification

Run this checklist only when you choose to make one real visitor booking. The local tests do not submit a booking.

1. Record the server's exact Home Assistant Core version and compare it with the locally tested `2026.11.0`. Record the installed Paysmarti Visitor version and repository tag. Check compatibility before proceeding if the server version differs.
2. Install the public, tagged integration through the Marketplace custom repository using the steps above. Confirm the dashboard card loads, the saved list refreshes, and **Enable saving new registrations** is off by default.
3. With a registration you are authorized to book and save, enable that option. Enter the registration as new text, check **Save this registration (unverified; controlled test only)**, and enter an optional nickname. Check the effective registration, permit, street, and intended day before selecting **Book** once.
4. Check the receipt and Paysmarti account for exactly one booking. If the card says **Outcome unknown**, check Paysmarti before any retry. Refresh the card and confirm the saved registration and nickname appear as intended; if either does not, record the discrepancy.
5. Turn the saving option off after the test. Record only redacted observations: Core and integration versions, the stage/result, whether the nickname and saved list refreshed, and any error code or HTTP status. Omit credentials, tokens, cookies, full registrations, names, addresses, query strings, page bodies, and unredacted screenshots or HAR files.

**Action permissions:** Anyone in Home Assistant who can run the `paysmarti_visitor.book_visitor` action for this config entry can create a real permit booking, including from the Actions interface without the card. Restrict access to trusted Home Assistant users and automations. The card does not store a typed registration in an entity state or browser storage. The integration stores a masked last attempt and leaves its persistent notification until dismissed. The card's **Dismiss** button hides the result only in the current card instance; reopening the card restores the backend result, and the Home Assistant notification remains until dismissed separately.

## Update and troubleshoot

Update **Paysmarti Visitor** from Marketplace on the Home Assistant server, restart Home Assistant, and reload the dashboard to refresh the card resource. If the card still uses an older file, clear the browser's cached dashboard resources and reload. A changed Paysmarti form can create a Home Assistant Repair issue; review it and update the integration before retrying. If the card reports **Outcome unknown**, check the Paysmarti site for the booking before making another attempt.

For a support report, download the integration's **Diagnostics** from **Settings → Devices & services → Paysmarti Visitor → ⋮ → Download diagnostics**. It contains only version, stage, error code, HTTP status, path without query, form field names, and last attempt status. Review the downloaded file before sharing. If sign-in or discovery fails during startup, Home Assistant may leave the entry unavailable, so the last-result action and diagnostics for that entry may be unavailable until setup succeeds; the recovered persistent notification remains visible. If a maintainer needs a fresh browser-flow capture, reproduce only with a disposable or controlled account and redact credentials, cookies, tokens, full registrations, names, addresses, query strings, and page bodies before sharing; never attach an unredacted HAR or screenshot.

## Development

Run `.venv-ha/bin/pytest -q` for Python tests and `npm ci && npm test` for the dashboard card tests. The card is a vanilla JavaScript module bundled under `custom_components/paysmarti_visitor/frontend/`; the integration serves it at the dashboard resource path above.
