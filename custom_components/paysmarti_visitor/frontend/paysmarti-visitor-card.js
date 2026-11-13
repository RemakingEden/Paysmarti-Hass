/* Paysmarti Visitor dashboard card. All form values live only in this element. */
(() => {
  const DOMAIN = 'paysmarti_visitor';
  const normalized = value => value.replace(/\s+/g, '').toUpperCase();
  const timestamp = value => value ? new Date(value).toLocaleString() : '';

  class PaysmartiVisitorCard extends HTMLElement {
    static getStubConfig() { return {}; }

    constructor() {
      super();
      this.attachShadow({mode: 'open'});
      this._busy = false;
      this._loadingInitial = false;
      this._canSave = false;
      this._vehicles = [];
      this._loadedFor = null;
      this._generation = 0;
      this._render();
    }

    setConfig(config) {
      if (!config || (config.config_entry_id !== undefined && typeof config.config_entry_id !== 'string')) {
        throw new Error('Invalid Paysmarti Visitor card configuration');
      }
      const entryId = config.config_entry_id || null;
      if (this._configured && this._entryId === entryId) return;
      this._configured = true;
      this._entryId = entryId;
      this._generation += 1;
      this._loadedFor = null;
      this._busy = false;
      this._loadingInitial = true;
      this._canSave = false;
      this._vehicles = [];
      this._lastBackendResult = null;
      this._saved.replaceChildren(new Option('Choose saved registration', ''));
      this._new.value = '';
      this._save.checked = false;
      this._nickname.value = '';
      this._listError.textContent = '';
      this._showResult({status: 'none'});
      this._update();
      this._load();
    }

    set hass(value) {
      this._hass = value;
      this._load();
    }

    getCardSize() { return 5; }

    async _action(service, data = {}) {
      const result = await this._hass.callWS({
        type: 'call_service', domain: DOMAIN, service,
        service_data: {...(this._entryId ? {config_entry_id: this._entryId} : {}), ...data}, return_response: true,
      });
      return result.response;
    }

    async _load() {
      if (!this._configured || !this._hass || this._loadedFor === (this._entryId || 'default')) return;
      this._loadedFor = this._entryId || 'default';
      const generation = this._generation;
      this._loadingInitial = true;
      this._update();
      await Promise.all([this._refresh(generation), this._restore(generation)]);
      if (generation !== this._generation) return;
      this._loadingInitial = false;
      this._update();
    }

    async _refresh(generation = this._generation) {
      if (!this._hass || this._busy || generation !== this._generation) return;
      this._busy = true;
      this._update();
      try {
        const data = await this._action('list_saved_registrations');
        if (generation !== this._generation) return;
        this._vehicles = Array.isArray(data?.vehicles) ? data.vehicles : [];
        this._canSave = data?.can_save_new === true;
        const selected = this._saved.value;
        this._saved.replaceChildren(new Option('Choose saved registration', ''));
        for (const vehicle of this._vehicles) {
          this._saved.add(new Option(vehicle.label, vehicle.id));
        }
        if (this._vehicles.some(vehicle => vehicle.id === selected)) this._saved.value = selected;
        this._listError.textContent = '';
      } catch (_error) {
        if (generation !== this._generation) return;
        this._listError.textContent = 'Saved registrations could not be loaded. Select Refresh to try again.';
      } finally {
        if (generation === this._generation) {
          this._busy = false;
          this._update();
        }
      }
    }

    async _restore(generation = this._generation) {
      try {
        const latest = await this._action('get_last_result');
        if (generation !== this._generation) return;
        this._lastBackendResult = JSON.stringify(latest);
        this._showResult(latest);
      } catch (_error) {
        if (generation === this._generation) this._showResult({status: 'outcome_unknown'});
      }
    }

    async _book() {
      if (this._busy || this._loadingInitial || this._bookButton.disabled) return;
      const generation = this._generation;
      const registration = normalized(this._new.value);
      const selected = this._saved.value;
      const data = registration
        ? {new_registration: registration, save: this._canSave && this._save.checked}
        : {saved_vehicle_id: selected};
      if (data.save && this._nickname.value.trim()) data.nickname = this._nickname.value.trim();
      this._busy = true;
      this._showResult({status: 'preparing'});
      this._update();
      // Snapshot the journal immediately before submission, including after a prior success.
      let previousResult = null;
      try {
        previousResult = JSON.stringify(await this._action('get_last_result'));
      } catch (_error) {
        // Without a baseline, a later journal read cannot prove which attempt it belongs to.
      }
      if (generation !== this._generation) return;
      try {
        const receipt = await this._action('book_visitor', data);
        if (generation !== this._generation) return;
        this._showResult({status: 'booked', registration: receipt.registration,
          safe_details: {starts_at: receipt.starts_at, ends_at: receipt.ends_at},
          reference: receipt.reference, refresh_warning: receipt.refresh_warning});
        this._new.value = '';
        this._save.checked = false;
        this._nickname.value = '';
      } catch (_error) {
        if (generation !== this._generation) return;
        // The journal is authoritative: an exception alone cannot identify the POST boundary.
        try {
          const latest = await this._action('get_last_result');
          if (generation !== this._generation) return;
          this._lastBackendResult = JSON.stringify(latest);
          const unchanged = previousResult !== null && this._lastBackendResult === previousResult;
          this._showResult(unchanged
            ? {status: 'not_booked', safe_details: {error_code: 'rejected_before_submission'}}
            : latest?.status === 'none' || previousResult === null
              ? {status: 'outcome_unknown'} : latest);
        } catch (_restoreError) {
          if (generation === this._generation) this._showResult({status: 'outcome_unknown'});
        }
      } finally {
        if (generation === this._generation) {
          this._busy = false;
          this._update();
        }
      }
      if (generation !== this._generation) return;
      if (data.save && this._result.dataset.kind === 'booked') await this._refresh();
    }

    _showResult(result) {
      const panel = this._result;
      panel.replaceChildren();
      panel.dataset.kind = result?.status || 'none';
      if (!result || result.status === 'none') {
        panel.hidden = true;
        return;
      }
      panel.hidden = false;
      const heading = document.createElement('strong');
      const detail = document.createElement('div');
      const safe = result.safe_details || {};
      if (result.status === 'booked') {
        heading.textContent = 'Booked';
        const parts = [result.registration || result.masked_registration,
          timestamp(safe.starts_at), timestamp(safe.ends_at), result.reference ? `Reference: ${result.reference}` : ''].filter(Boolean);
        detail.textContent = parts.join(' · ');
        if (result.refresh_warning) detail.textContent += ' · Saved list refresh failed; select Refresh.';
      } else if (result.status === 'not_booked') {
        heading.textContent = 'Not booked';
        detail.textContent = `The final booking was not completed. ${safe.error_code ? `Reason: ${safe.error_code.replace(/_/g, ' ')}. ` : ''}Correct the input or refresh and try again.`;
      } else if (result.status === 'outcome_unknown' || result.status === 'commit_started') {
        heading.textContent = 'Outcome unknown';
        detail.textContent = 'Check Paysmarti before trying again. The booking request may have been submitted.';
      } else {
        heading.textContent = 'Booking in progress';
        detail.textContent = 'Waiting for the booking result.';
      }
      const dismiss = document.createElement('button');
      dismiss.id = 'dismiss-result';
      dismiss.type = 'button';
      dismiss.setAttribute('aria-label', 'Dismiss booking result');
      dismiss.textContent = 'Dismiss';
      dismiss.addEventListener('click', () => { panel.hidden = true; });
      panel.append(heading, detail, dismiss);
    }

    _update() {
      const entered = this._new.value.trim();
      const registration = normalized(entered);
      const valid = !entered || /^[A-Z0-9]{1,10}$/.test(registration);
      const chosen = this._vehicles.find(vehicle => vehicle.id === this._saved.value);
      const effective = entered ? registration : chosen?.registration;
      this._effective.textContent = effective ? `Effective registration: ${effective}` : 'Choose or enter a registration';
      this._validation.textContent = valid ? '' : 'Use 1–10 letters and numbers for a new registration.';
      this._saveRow.hidden = !this._canSave || !entered;
      if (this._saveRow.hidden) {
        this._save.checked = false;
        this._nickname.value = '';
      }
      this._nicknameRow.hidden = this._saveRow.hidden || !this._save.checked;
      if (this._nicknameRow.hidden) this._nickname.value = '';
      this._bookButton.disabled = this._busy || this._loadingInitial || !valid || !effective;
      this._refreshButton.disabled = this._busy;
      this._saved.disabled = this._busy;
      this._new.disabled = this._busy;
      this._save.disabled = this._busy;
      this._nickname.disabled = this._busy;
    }

    _render() {
      this.shadowRoot.innerHTML = `
        <style>
          :host { display:block; color:var(--primary-text-color, #222); }
          ha-card { display:block; padding:20px; background:var(--card-background-color, white); border-radius:12px; box-shadow:var(--ha-card-box-shadow, 0 2px 6px #0002); }
          h2 { margin:0 0 16px; font-size:1.25rem; }
          label { display:block; margin:12px 0 4px; font-weight:600; }
          input:not([type=checkbox]), select { box-sizing:border-box; width:100%; min-height:40px; padding:8px; border:1px solid var(--divider-color, #888); border-radius:6px; background:var(--card-background-color, white); color:inherit; font:inherit; }
          .check { display:flex; align-items:center; gap:8px; font-weight:normal; }
          .actions { display:flex; gap:8px; margin-top:14px; }
          button { min-height:40px; padding:8px 16px; border:0; border-radius:6px; font:inherit; cursor:pointer; }
          button:disabled { opacity:.55; cursor:default; }
          #book { background:var(--primary-color, #21618c); color:white; }
          #refresh { background:var(--secondary-background-color, #eee); color:inherit; }
          #effective { margin-top:14px; font-weight:600; }
          #validation, #list-error { color:var(--error-color, #b00020); }
          #result { margin-top:18px; padding:14px; border-radius:6px; border:2px solid; }
          #result[hidden], [hidden] { display:none !important; }
          #result[data-kind=booked] { border-color:#278a44; background:#e8f5e9; color:#124522; }
          #result[data-kind=not_booked] { border-color:#c62828; background:#ffebee; color:#7f1212; }
          #result[data-kind=outcome_unknown], #result[data-kind=commit_started] { border-color:#b87300; background:#fff3cd; color:#624000; }
          #result[data-kind=preparing] { border-color:#4d6f95; background:#eaf2fa; color:#1c3956; }
          #result strong { display:block; margin-bottom:6px; }
          #dismiss-result { margin-top:10px; background:transparent; border:1px solid currentColor; color:inherit; }
        </style>
        <ha-card><h2>Paysmarti Visitor</h2>
          <label for="saved">Saved registration</label><select id="saved"><option value="">Choose saved registration</option></select>
          <label for="new">New registration</label><input id="new" type="text" autocomplete="off" maxlength="20" autocapitalize="characters" spellcheck="false">
          <div id="validation" role="alert"></div>
          <div id="save-row" hidden><label class="check"><input id="save" type="checkbox"> Save this registration</label></div>
          <div id="nickname-row" hidden><label for="nickname">Nickname (optional)</label><input id="nickname" type="text" autocomplete="off"></div>
          <div id="effective" aria-live="polite"></div>
          <div class="actions"><button id="book" type="button">Book</button><button id="refresh" type="button">Refresh</button></div>
          <div id="list-error" role="alert"></div>
          <div id="result" role="status" aria-live="polite" aria-atomic="true" hidden></div>
        </ha-card>`;
      const q = selector => this.shadowRoot.querySelector(selector);
      this._saved = q('#saved'); this._new = q('#new'); this._save = q('#save');
      this._nickname = q('#nickname'); this._saveRow = q('#save-row'); this._nicknameRow = q('#nickname-row');
      this._bookButton = q('#book'); this._refreshButton = q('#refresh');
      this._effective = q('#effective'); this._validation = q('#validation');
      this._listError = q('#list-error'); this._result = q('#result');
      this._saved.addEventListener('change', () => this._update());
      this._new.addEventListener('input', () => this._update());
      this._save.addEventListener('change', () => this._update());
      this._bookButton.addEventListener('click', () => this._book());
      this._refreshButton.addEventListener('click', () => this._refresh());
      this._update();
    }
  }
  customElements.define('paysmarti-visitor-card', PaysmartiVisitorCard);
  window.customCards = window.customCards || [];
  if (!window.customCards.some(card => card.type === 'paysmarti-visitor-card')) {
    window.customCards.push({
      type: 'paysmarti-visitor-card',
      name: 'Paysmarti Visitor',
      description: 'Book a visitor parking session for today.',
      preview: false,
      documentationURL: 'https://github.com/RemakingEden/Paysmarti-Hass',
    });
  }
})();
