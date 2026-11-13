"""Diagnostics and Repair issue keep secret material out."""

import pytest
import asyncio
from types import SimpleNamespace

from custom_components.paysmarti_visitor.models import UnexpectedPage
from custom_components.paysmarti_visitor.diagnostics import async_get_config_entry_diagnostics
from custom_components.paysmarti_visitor.issues import record_page_error
from custom_components.paysmarti_visitor.services import list_saved_registrations


@pytest.mark.asyncio
async def test_diagnostics_export_only_safe_error_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_create_issue", lambda *a, **kw: calls.append((a, kw)))
    secret = "AA00AAA password token body"
    state = SimpleNamespace(last_error=None, lock=asyncio.Lock(), session=object(), context=object(), enable_save_new=False)
    entry = SimpleNamespace(entry_id="one", runtime_data=state, data={"password": secret})
    state.entry = entry
    record_page_error(object(), entry, UnexpectedPage("summary", "/StepTwo?token=secret", ("Vrm", "SaveThisVrm")))
    data = await async_get_config_entry_diagnostics(None, entry)
    assert data == {"version": "0.1.0", "stage": "summary", "code": "form_changed", "http_status": None,
                    "path": "/StepTwo", "field_names": ["Vrm", "SaveThisVrm"]}
    assert secret not in str(data) and "token=secret" not in str(data)
    assert len(calls) == 1
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_delete_issue", lambda *a, **kw: calls.append((a, kw)))
    async def vehicles(session, context):
        return []
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    await list_saved_registrations(object(), state)
    assert len(calls) == 2
    assert (await async_get_config_entry_diagnostics(None, entry))["stage"] is None


@pytest.mark.asyncio
async def test_compatible_operation_clears_issue_after_runtime_restart(monkeypatch):
    deleted = []
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_delete_issue", lambda *a: deleted.append(a))
    async def vehicles(session, context):
        return []
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    state = SimpleNamespace(last_error=None, lock=asyncio.Lock(), session=object(), context=object(), enable_save_new=False)
    state.entry = SimpleNamespace(entry_id="one", runtime_data=state)
    await list_saved_registrations(object(), state)
    assert len(deleted) == 1
    assert deleted[0][1:] == ("paysmarti_visitor", "form_changed_one")
