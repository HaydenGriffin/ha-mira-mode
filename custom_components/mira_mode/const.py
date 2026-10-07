"""Constants for the Mira Mode integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mira_mode"
MANUFACTURER: Final = "Mira Showers"
MODEL: Final = "Mira Mode digital valve"

CONF_SERIAL: Final = "serial"
CONF_OUTLET_LABELS: Final = ("outlet_1", "outlet_2", "outlet_3")
DEFAULT_OUTLET_LABELS: Final = {
    "outlet_1": "Outlet 1",
    "outlet_2": "Outlet 2",
    "outlet_3": "",
}

#: Idle: connect, read, disconnect once a minute, so the valve stays free
#: for the phone app most of the time.
IDLE_INTERVAL: Final = timedelta(seconds=60)
#: Running: stay connected and read every 10 seconds.
RUNNING_INTERVAL: Final = timedelta(seconds=10)

RESPONSE_TIMEOUT: Final = 5.0

#: Hot water limits for anything Home Assistant asks for.
MIN_TEMPERATURE: Final = 30.0
MAX_TEMPERATURE: Final = 45.0
DEFAULT_TEMPERATURE: Final = 39.0
TEMPERATURE_STEP: Final = 0.5
MIN_FLOW: Final = 1
DEFAULT_FLOW: Final = 100

SERVICE_START: Final = "start"
ATTR_TEMPERATURE: Final = "temperature"
ATTR_FLOW: Final = "flow"
