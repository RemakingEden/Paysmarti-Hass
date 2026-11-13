"""The installed integration exposes its bundled dashboard module."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.paysmarti_visitor import async_setup


@pytest.mark.asyncio
async def test_setup_registers_bundled_card_at_dashboard_resource_path():
    registrations = []
    async def register(paths):
        registrations.extend(paths)
    hass = SimpleNamespace(
        http=SimpleNamespace(async_register_static_paths=register),
        services=SimpleNamespace(async_register=lambda *args, **kwargs: None),
    )
    assert await async_setup(hass, {})
    assert len(registrations) == 1
    resource = registrations[0]
    assert resource.url_path == '/api/paysmarti_visitor/paysmarti-visitor-card.js'
    assert Path(resource.path).is_file()
    assert b'customElements.define' in Path(resource.path).read_bytes()
