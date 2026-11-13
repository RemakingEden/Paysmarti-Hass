"""The installed integration exposes its bundled dashboard module."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.paysmarti_visitor import async_setup


@pytest.fixture(autouse=True)
def integration_version(monkeypatch):
    from homeassistant import loader

    async def integration(hass, domain):
        return SimpleNamespace(version="0.1.8")
    monkeypatch.setattr(loader, "async_get_integration", integration)


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


@pytest.mark.asyncio
async def test_setup_adds_card_resource_to_dashboard_once():
    resources = []
    async def register(paths):
        pass
    async def create(item):
        resources.append(item)
    async def info():
        return {"resources": len(resources)}
    collection = SimpleNamespace(async_items=lambda: resources, async_create_item=create,
                                 async_get_info=info)
    hass = SimpleNamespace(
        http=SimpleNamespace(async_register_static_paths=register),
        services=SimpleNamespace(async_register=lambda *args, **kwargs: None),
        data={"lovelace": SimpleNamespace(resource_mode="storage", resources=collection)},
    )
    await async_setup(hass, {})
    assert len(resources) == 1


@pytest.mark.asyncio
async def test_setup_updates_existing_card_resource_without_adding_another():
    resources = [{"id": "old", "url": "/api/paysmarti_visitor/paysmarti-visitor-card.js", "res_type": "module"}]
    async def register(paths):
        pass
    async def info():
        return {"resources": len(resources)}
    async def create(item):
        resources.append(item)
    async def update(item_id, changes):
        assert item_id == "old"
        resources[0].update(changes)
    collection = SimpleNamespace(async_items=lambda: resources, async_get_info=info,
                                 async_create_item=create, async_update_item=update)
    hass = SimpleNamespace(
        http=SimpleNamespace(async_register_static_paths=register),
        services=SimpleNamespace(async_register=lambda *args, **kwargs: None),
        data={"lovelace": SimpleNamespace(resource_mode="storage", resources=collection)},
    )
    await async_setup(hass, {})
    assert len(resources) == 1
    assert resources[0]["url"].endswith("?v=0.1.8")


@pytest.mark.asyncio
async def test_setup_gets_resource_version_without_blocking_file_read(monkeypatch):
    from homeassistant import loader

    async def integration(hass, domain):
        assert domain == "paysmarti_visitor"
        return SimpleNamespace(version="0.1.8")
    def forbidden_read(*args, **kwargs):
        raise AssertionError("manifest file read in event loop")
    monkeypatch.setattr(loader, "async_get_integration", integration)
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    resources = []
    async def register(paths):
        pass
    async def info():
        return {"resources": 0}
    async def create(item):
        resources.append(item)
    hass = SimpleNamespace(
        http=SimpleNamespace(async_register_static_paths=register),
        services=SimpleNamespace(async_register=lambda *args, **kwargs: None),
        data={"lovelace": SimpleNamespace(resource_mode="storage", resources=SimpleNamespace(
            async_items=lambda: resources, async_get_info=info, async_create_item=create))},
    )
    await async_setup(hass, {})
    assert resources[0]["url"].endswith("?v=0.1.8")
    assert resources[0]["res_type"] == "module"
    assert resources[0]["url"].startswith("/api/paysmarti_visitor/paysmarti-visitor-card.js")
    await async_setup(hass, {})
    assert len(resources) == 1
