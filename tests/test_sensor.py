"""The extra reads as sensors: supply, last run, outlet settings, totals and the usage event."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, State as HaState, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.mira_mode.client import Reading
from custom_components.mira_mode.coordinator import heating_energy
from custom_components.mira_mode.protocol import (
    Usage,
    decode_inlets,
    decode_last_usage,
    decode_outlet_settings,
    decode_state,
)

from . import captures
from .conftest import IDLE

INLETS = decode_inlets(captures.INLETS)
LAST_USAGE = decode_last_usage(captures.LAST_USAGE)
SETTINGS = decode_outlet_settings(captures.OUTLET_SETTINGS)
RAMPING = decode_state(captures.STATE_RAMPING)
assert INLETS and LAST_USAGE and SETTINGS and RAMPING

#: A run that finished after LAST_USAGE.
NEXT_USAGE = Usage(seconds=600, litres=120, temperature=45.0)

WATER_USED = "sensor.mira_demo_water_used"
ENERGY = "sensor.mira_demo_water_heating_energy"
HOT_INLET = "sensor.mira_demo_hot_inlet_temperature"


def reading(**changes: object) -> Reading:
    """An idle poll the valve answered in full, with ``changes``."""
    full: dict[str, object] = {
        "state": IDLE,
        "inlets": INLETS,
        "last_usage": LAST_USAGE,
        "outlet_settings": {1: SETTINGS, 2: SETTINGS},
    }
    return Reading(**{**full, **changes})  # type: ignore[arg-type]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def test_extra_reads_become_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    valve.read.return_value = reading()
    await _setup(hass, config_entry)
    assert _state(hass, "sensor.mira_demo_cold_inlet_temperature") == "20.4"
    assert _state(hass, "sensor.mira_demo_flow_rate") == "0.0"
    assert _state(hass, "sensor.mira_demo_last_run_duration") == "10"
    assert _state(hass, "sensor.mira_demo_last_run_water_used") == "1"
    assert _state(hass, "sensor.mira_demo_last_run_temperature") == "44.0"
    assert _state(hass, "sensor.mira_demo_outlet_1_max_temperature") == "45.0"
    assert _state(hass, "sensor.mira_demo_outlet_1_max_duration") == "30.0"
    assert _state(hass, "sensor.mira_demo_outlet_2_default_flow") == "12.0"
    # Nothing has finished since setup, so nothing has been counted.
    assert _state(hass, WATER_USED) == "0.0"


async def test_flow_rate_while_running(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, config_entry)
    valve.read.return_value = reading(state=RAMPING)
    await _poll(hass, freezer, 60)
    assert _state(hass, "sensor.mira_demo_flow_rate") == "7.5"


async def test_unanswered_reads_keep_their_last_value(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    valve.read.return_value = reading()
    await _setup(hass, config_entry)
    valve.read.return_value = Reading(IDLE)
    await _poll(hass, freezer, 60)
    assert _state(hass, "sensor.mira_demo_cold_inlet_temperature") == "20.4"
    assert _state(hass, "sensor.mira_demo_last_run_duration") == "10"
    assert _state(hass, "sensor.mira_demo_outlet_1_max_temperature") == "45.0"


async def test_outlet_settings_are_read_every_half_hour(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, config_entry)
    assert valve.read.await_args.args[1:] == ([1, 2],)
    await _poll(hass, freezer, 60)
    assert valve.read.await_args.args[1:] == ([],)
    await _poll(hass, freezer, 30 * 60)
    assert valve.read.await_args.args[1:] == ([1, 2],)


async def test_hot_inlet_is_off_by_default_and_carries_the_raw_reply(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    valve.read.return_value = reading()
    await _setup(hass, config_entry)
    entry = entity_registry.async_get(HOT_INLET)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(HOT_INLET) is None

    entity_registry.async_update_entity(HOT_INLET, disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(HOT_INLET)
    assert state is not None
    assert state.state == "36.1"
    assert state.attributes["raw"] == captures.INLETS.hex(" ")


async def test_a_finished_run_is_counted_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
    device_registry: dr.DeviceRegistry,
) -> None:
    events: list[dict[str, object]] = []

    @callback
    def on_usage(event: Event) -> None:
        events.append(dict(event.data))

    hass.bus.async_listen("mira_mode_usage", on_usage)
    valve.read.return_value = reading()
    await _setup(hass, config_entry)  # the record found at setup is only a baseline

    valve.read.return_value = reading(state=RAMPING, inlets=None)
    await _poll(hass, freezer, 60)
    valve.read.return_value = reading(inlets=None, last_usage=NEXT_USAGE)
    await _poll(hass, freezer, 10)

    energy = heating_energy(NEXT_USAGE, INLETS.cold)  # the last cold inlet read
    assert energy is not None and 3.4 < energy < 3.5
    [device] = dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
    assert events == [
        {
            "device_id": device.id,
            "seconds": 600,
            "litres": 120,
            "temperature": 45.0,
            "energy_kwh": energy,
        }
    ]
    assert _state(hass, WATER_USED) == "120.0"
    assert float(_state(hass, ENERGY)) == energy
    assert _state(hass, "sensor.mira_demo_last_run_duration") == "600"

    # The same record again, or a command's reply, adds nothing.
    await _poll(hass, freezer, 60)
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: "switch.mira_demo_outlet_1"}, blocking=True
    )
    valve.set_outlets.assert_awaited_once()
    assert _state(hass, WATER_USED) == "120.0"
    assert len(events) == 1


async def test_a_run_without_a_cold_inlet_adds_water_but_no_energy(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    valve.read.return_value = reading(inlets=None)
    await _setup(hass, config_entry)
    valve.read.return_value = reading(inlets=None, last_usage=NEXT_USAGE)
    await _poll(hass, freezer, 60)
    assert _state(hass, WATER_USED) == "120.0"
    assert _state(hass, ENERGY) == "0.0"


async def test_totals_carry_on_after_a_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    valve: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                HaState(WATER_USED, "500.5"),
                {"native_value": 500.5, "native_unit_of_measurement": "L"},
            ),
            (
                HaState(ENERGY, "unknown"),
                {"native_value": "not a number", "native_unit_of_measurement": "kWh"},
            ),
        ],
    )
    valve.read.return_value = reading()
    await _setup(hass, config_entry)
    assert _state(hass, WATER_USED) == "500.5"
    assert _state(hass, ENERGY) == "0.0"

    valve.read.return_value = reading(last_usage=NEXT_USAGE)
    await _poll(hass, freezer, 60)
    assert _state(hass, WATER_USED) == "620.5"


async def test_unlabelled_outlets_lose_their_settings_sensors(
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
    assert entity_registry.async_get("sensor.mira_demo_outlet_2_max_temperature") is None
    assert valve.read.await_args.args[1:] == ([1],)
    # The label names the sensor; the entity id stays stable.
    state = hass.states.get("sensor.mira_demo_outlet_1_max_temperature")
    assert state is not None
    assert state.attributes["friendly_name"] == "Mira Demo Shower max temperature"


async def test_diagnostics_include_the_extra_reads(
    hass: HomeAssistant, config_entry: MockConfigEntry, valve: AsyncMock
) -> None:
    from custom_components.mira_mode.diagnostics import async_get_config_entry_diagnostics

    valve.read.return_value = reading()
    await _setup(hass, config_entry)
    config_entry.runtime_data.client.unanswered.add((0x5B, b"\x03\x00"))
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["inlets"]["raw"] == captures.INLETS.hex(" ")
    assert result["last_usage"] == {"seconds": 10, "litres": 1, "temperature": 44.0}
    assert result["outlet_settings"]["1"]["max_duration"] == 1800
    assert result["unanswered_reads"] == ["5b 03 00"]
