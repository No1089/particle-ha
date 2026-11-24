"""The setup dialog up to the device list, with Particle's login and account stubbed."""
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.particle.const import DOMAIN

DEVICES = [
    {"id": "e00fce68aaaaaaaaaaaaaaaa", "name": "Garden", "platform_id": 13, "product_id": 13, "online": True},
    {"id": "0a10aced1111111111111111", "name": "Snowflake", "platform_id": 32, "product_id": 17910, "online": True},
    {"id": "0a10aced2222222222222222", "name": "spare", "platform_id": 32, "product_id": 32, "online": False},
]


class FakeAccount:
    def __init__(self, *a, **k):
        self.logged_out = False

    async def devices(self):
        return DEVICES

    async def products(self):
        return [{"id": 17910, "name": "Test P2", "slug": "test-p2", "platform_id": 32}]

    async def logout(self):
        self.logged_out = True


async def test_find_then_pick(hass):
    account = FakeAccount()
    with patch("custom_components.particle.config_flow.login", return_value="login-token"), \
         patch("custom_components.particle.config_flow.ParticleAccount", return_value=account):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert r["step_id"] == "login"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"username": "a@b.c", "password": "x"})
        assert r["step_id"] == "find"
        platforms = [o["label"] for o in r["data_schema"].schema["platform"].config["options"]]
        assert platforms[0] == "All platforms (3)" and "P2 / Photon 2 (2)" in platforms

        # nothing matches: stay on find with an error
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"platform": "13", "search": "snow"})
        assert r["step_id"] == "find" and r["errors"] == {"base": "no_match"}

        # P2 only, online only: just the Snowflake, plus "search again"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"platform": "32", "search": "", "online": True})
        assert r["step_id"] == "device"
        values = [o["value"] for o in r["data_schema"].schema["device"].config["options"]]
        assert values == ["0a10aced1111111111111111", "__search__"]

        # search by the end of a device ID
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"device": "__search__"})
        assert r["step_id"] == "find"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"platform": "all", "search": "2222", "online": False})
        values = [o["value"] for o in r["data_schema"].schema["device"].config["options"]]
        assert values == ["0a10aced2222222222222222", "__search__"]

        hass.config_entries.flow.async_abort(r["flow_id"])
        await hass.async_block_till_done()
    assert account.logged_out  # cancelling deletes the login token
    assert r["type"] == FlowResultType.FORM
