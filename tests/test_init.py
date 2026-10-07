"""Setting up, the entities, the actions and unloading."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import ANY, AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.mira_mode.client import CommandRejected, ValveNotFound, ValveUnreachable
from custom_components.mira_mode.const import DOMAIN
from custom_components.mira_mode.protocol import State

from .conftest import ADDRESS, IDLE, RUNNING, SERIAL

SWITCH_1 = "switch.mira_demo_outlet_1"
SWITCH_2 = "switch.mira_demo_outlet_2"
TEMPERATURE = "number.mira_demo_temperature"
FLOW = "number.mira_demo_flow"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_the_device_and_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    [device] = dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
    assert device.connections == {(dr.CONNECTION_BLUETOOTH, ADDRESS)}
    assert device.name == "Mira Demo"
    assert device.serial_number == SERIAL
    assert device.manufacturer == "Mira Showers"

    entities = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert sorted(e.entity_id for e in entities) == [
        "binary_sensor.mira_demo_running",
        "button.mira_demo_stop",
        FLOW,
        TEMPERATURE,
        "sensor.mira_demo_water_temperature",
        SWITCH_1,
        SWITCH_2,
    ]
    assert hass.states.get("binary_sensor.mira_demo_running").state == STATE_OFF
    assert hass.states.get("sensor.mira_demo_water_temperature").state == "25.6"
    assert hass.states.get(SWITCH_1).state == STATE_OFF
    assert hass.states.get(TEMPERATURE).state == "39.0"
    assert hass.states.get(FLOW).state == "100"


async def test_setup_retries_while_the_valve_is_out_of_reach(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    valve.read_state.side_effect = ValveNotFound(ADDRESS)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_running_valve_is_polled_faster_and_followed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, config_entry)
    valve.read_state.return_value = RUNNING  # started on the dial: 42.0 C, flow 50

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.mira_demo_running").state == STATE_ON
    assert hass.states.get(SWITCH_1).state == STATE_ON
    assert hass.states.get(SWITCH_2).state == STATE_OFF
    assert hass.states.get(TEMPERATURE).state == "42.0"
    assert hass.states.get(FLOW).state == "50"

    calls = valve.read_state.await_count
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert valve.read_state.await_count == calls + 1


async def test_poll_failure_marks_entities_unavailable_but_keeps_controls(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, config_entry)
    valve.read_state.side_effect = ValveUnreachable("timeout")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_1).state == STATE_UNAVAILABLE
    assert hass.states.get("button.mira_demo_stop").state != STATE_UNAVAILABLE
    assert hass.states.get(TEMPERATURE).state != STATE_UNAVAILABLE


async def test_outlet_switch_runs_at_the_settings(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: TEMPERATURE, "value": 40.5}, blocking=True
    )
    valve.set_outlets.assert_not_awaited()  # idle: settings only

    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_2}, blocking=True)
    valve.set_outlets.assert_awaited_once_with(ANY, 2, 40.5, 100)
    assert hass.states.get(SWITCH_2).state == STATE_ON

    # Adding outlet 1 keeps outlet 2 running.
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True)
    valve.set_outlets.assert_awaited_with(ANY, 3, 40.5, 100)

    # Removing outlet 2 keeps outlet 1 running.
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SWITCH_2}, blocking=True)
    valve.set_outlets.assert_awaited_with(ANY, 1, 40.5, 100)

    # Turning the last one off stops, leaving the valve's temperature alone.
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True)
    valve.set_outlets.assert_awaited_with(ANY, 0, 0.0, 0)
    assert hass.states.get("binary_sensor.mira_demo_running").state == STATE_OFF


async def test_changing_a_setting_while_running_applies_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: FLOW, "value": 60}, blocking=True
    )
    valve.set_outlets.assert_awaited_with(ANY, 1, 39.0, 60)
    assert hass.states.get(FLOW).state == "60"


async def test_stop_button(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True)
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.mira_demo_stop"}, blocking=True
    )
    valve.set_outlets.assert_awaited_with(ANY, 0, 0.0, 0)
    assert hass.states.get(SWITCH_1).state == STATE_OFF


async def test_start_action(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "start",
        {ATTR_ENTITY_ID: SWITCH_1, "temperature": 41, "flow": 80},
        blocking=True,
    )
    valve.set_outlets.assert_awaited_once_with(ANY, 1, 41.0, 80)
    assert hass.states.get(TEMPERATURE).state == "41.0"
    assert hass.states.get(FLOW).state == "80"


@pytest.mark.parametrize("temperature", [29.5, 45.5])
async def test_start_action_refuses_unsafe_temperatures(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock, temperature: float
) -> None:
    await _setup(hass, config_entry)
    with pytest.raises(Exception, match="value must be"):
        await hass.services.async_call(
            DOMAIN, "start", {ATTR_ENTITY_ID: SWITCH_1, "temperature": temperature}, blocking=True
        )
    valve.set_outlets.assert_not_awaited()


@pytest.mark.parametrize(
    ("side_effect", "match"),
    [
        (CommandRejected("80"), "rejected"),
        (ValveNotFound(ADDRESS), "can see the valve"),
        (ValveUnreachable("timeout"), "Couldn't talk"),
    ],
)
async def test_command_failures_raise(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    side_effect: Exception,
    match: str,
) -> None:
    await _setup(hass, config_entry)
    valve.set_outlets.side_effect = side_effect
    with pytest.raises(HomeAssistantError, match=match):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True
        )


async def test_valve_not_following_the_command_raises(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    await _setup(hass, config_entry)
    valve.set_outlets.side_effect = None
    valve.set_outlets.return_value = IDLE  # acknowledged, but nothing runs
    with pytest.raises(HomeAssistantError, match="isn't running"):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: SWITCH_1}, blocking=True
        )


async def test_settings_are_restored(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    from homeassistant.core import State as HaState
    from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                HaState(TEMPERATURE, "43.0"),
                {
                    "native_value": 43.0,
                    "native_unit_of_measurement": "°C",
                    "native_min_value": 30.0,
                    "native_max_value": 45.0,
                    "native_step": 0.5,
                },
            ),
            (
                HaState(FLOW, "99"),
                {
                    "native_value": 120,
                    "native_unit_of_measurement": "%",
                    "native_min_value": 1,
                    "native_max_value": 100,
                    "native_step": 1,
                },
            ),
        ],
    )
    await _setup(hass, config_entry)
    assert hass.states.get(TEMPERATURE).state == "43.0"
    assert hass.states.get(FLOW).state == "100"  # out-of-range value ignored


async def test_options_hide_an_outlet(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    await _setup(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, options={"outlet_1": "Shower", "outlet_2": "", "outlet_3": ""}
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_2) is None
    assert entity_registry.async_get(SWITCH_2) is None
    # The label is the entity name; the entity id stays stable.
    assert hass.states.get(SWITCH_1).attributes["friendly_name"] == "Mira Demo Shower"


async def test_unload(hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redact_the_address(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    from custom_components.mira_mode.diagnostics import async_get_config_entry_diagnostics

    await _setup(hass, config_entry)
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["entry"]["data"]["address"] == "**REDACTED**"
    assert result["entry"]["data"]["serial"] == "**REDACTED**"
    assert result["state"]["raw"].startswith("21 20")


def test_running_fixture_matches_the_capture() -> None:
    assert isinstance(RUNNING, State) and RUNNING.outlets == 1
