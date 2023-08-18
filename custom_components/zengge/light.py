"""Platform for light integration."""
from __future__ import annotations

import logging
import math

from .zengge_mesh import ZenggeMesh
from typing import Any, Dict, Optional

import homeassistant.util.color as color_util
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.entity import DeviceInfo, Entity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_RGB_COLOR,
    LightEntity,
    ColorMode
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_DEVICES,
    CONF_MAC,

    STATE_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
)
from .const import DOMAIN, CONF_MESH_ID, CONF_MANUFACTURER, CONF_MODEL, CONF_FIRMWARE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    _LOGGER.debug('entry %s', entry.data[CONF_DEVICES])

    mesh = hass.data[DOMAIN][entry.entry_id]
    lights = []
    for device in entry.data[CONF_DEVICES]:
        # Skip non lights
        if 'light' not in device['type']:
            continue
        if CONF_MANUFACTURER not in device:
            device[CONF_MANUFACTURER] = None
        if CONF_MODEL not in device:
            device[CONF_MODEL] = None
        if CONF_FIRMWARE not in device:
            device[CONF_FIRMWARE] = None

        type_string = ''
        supported_color_modes = set()

        if 'type' in device:
            type_string = device['type']

        if 'color' in type_string:
            supported_color_modes.add(ColorMode.RGB)

        if 'temperature' in type_string:
            supported_color_modes.add(ColorMode.COLOR_TEMP)

        if 'dimming' in type_string:
            supported_color_modes.add(ColorMode.BRIGHTNESS)

        if len(supported_color_modes) == 0:
            supported_color_modes.add(ColorMode.ONOFF)

        light = ZenggeLight(mesh, device[CONF_MAC], device[CONF_MESH_ID], device[CONF_NAME], supported_color_modes,
                          device[CONF_MANUFACTURER], device[CONF_MODEL], device[CONF_FIRMWARE])
        _LOGGER.info('Setup light [%d] %s', device[CONF_MESH_ID], device[CONF_NAME])

        lights.append(light)

    async_add_entities(lights)

def convert_value_to_available_range(value, min_from, max_from, min_to, max_to) -> int:
    normalized = (value - min_from) / (max_from - min_from)
    new_value = min(
        round((normalized * (max_to - min_to)) + min_to),
        max_to,
    )
    return max(new_value, min_to)


def normal_round(n):
    if n - math.floor(n) < 0.5:
        return math.floor(n)
    return math.ceil(n)


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def saturate(value):
    return clamp(value, 0.0, 1.0)


def hue_to_rgb(h):
    r = abs(h * 6.0 - 3.0) - 1.0
    g = 2.0 - abs(h * 6.0 - 2.0)
    b = 2.0 - abs(h * 6.0 - 4.0)
    return saturate(r), saturate(g), saturate(b)


def hsl_to_rgb(h, s=1, l=.5):
    h = (h/360)
    r, g, b = hue_to_rgb(h)
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    r = round((r - 0.5) * c + l,4) * 255
    g = round((g - 0.5) * c + l,4) * 255
    b = round((b - 0.5) * c + l,4) * 255
    if (r >= 250):
        r = 255
    if (g >= 250):
        g = 255
    if (b >= 250):
        b = 255
    return round(r), round(g), round(b)


def h360_to_h255(h360):
    if h360 <= 180:
        return normal_round((h360*254)/360)
    else:
        return normal_round((h360*255)/360)


def h255_to_h360(h255):
    if h255 <= 128:
        return normal_round((h255*360)/254)
    else:
        return normal_round((h255*360)/255)


class ZenggeLight(CoordinatorEntity, LightEntity):
    """Representation of an Awesome Light."""

    def __init__(self, coordinator: ZenggeMesh, mac: str, mesh_id: int, name: str, supported_color_modes: set[str] | None,
                 manufacturer: str, model: str, firmware: str):
        """Initialize an Zengge MESH Light."""
        super().__init__(coordinator)
        self._mesh = coordinator
        self._mac = mac
        self._mesh_id = mesh_id

        self._attr_name = name
        self._attr_unique_id = "zenggemesh-%s" % self._mesh_id
        self._attr_supported_color_modes = supported_color_modes

        self._manufacturer = manufacturer
        self._model = model
        self._firmware = firmware

        self._mesh.register_device(mesh_id, mac, name, self.status_callback)

        self._state = None
        self._color_mode = False
        self._red = None
        self._green = None
        self._blue = None
        self._white_temperature = None
        self._white_brightness = None
        self._color_brightness = None

    @property
    def device_info(self) -> DeviceInfo:
        """Get device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer=self._manufacturer,
            model=self._model.replace('_', ' '),
            sw_version=self._firmware,
            via_device=(DOMAIN, self._mesh.identifier),
        )

    @property
    def icon(self) -> Optional[str]:
        if 'Spot' in self._model:
            return 'mdi:wall-sconce-flat'
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self._state is None:
            return False
        return True

    @property
    def state(self) -> StateType:
        """Return the state of the entity."""
        if self._state is None:
            return STATE_UNAVAILABLE

        return STATE_ON if self.is_on else STATE_OFF

    @property
    def rgb_color(self):
        """Return color when in color mode"""
        return (
            self._red,
            self._green,
            self._blue
        )

    @property
    def color_temp(self):
        """Return the color temperature in mired."""
        if self._white_temperature is None:
            return None
        return convert_value_to_available_range(self._white_temperature, 0, int(0x64), self.min_mireds, self.max_mireds)

    @property
    def brightness(self):
        """Return the brightness of the light."""
        if self.color_mode != ColorMode.RGB:
            if self._white_brightness is None:
                return None
            return convert_value_to_available_range(self._white_brightness, int(1), int(0x64), 0, 255)

        if self._color_brightness is None:
            return None

        return convert_value_to_available_range(self._color_brightness, int(1), int(0x64), 0, 255)

    @property
    def min_mireds(self):
        # 6500 Kelvin
        return 153

    @property
    def max_mireds(self):
        # 2700 Kelvin
        return 370

    @property
    def is_on(self):
        """Return true if light is on."""
        return bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Instruct the light to turn on."""
        status = {}

        _LOGGER.debug('[%s] Turn on %s', self.unique_id, kwargs)

        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            await self._mesh.async_set_color(self._mesh_id, rgb[0], rgb[1], rgb[2])
            status['red'] = rgb[0]
            status['green'] = rgb[1]
            status['blue'] = rgb[2]
            status['state'] = True

        if ATTR_BRIGHTNESS in kwargs:
            status['state'] = True
            if self.color_mode != ColorMode.RGB:
                device_brightness = convert_value_to_available_range(kwargs[ATTR_BRIGHTNESS], 0, 255, int(1), int(0x64))
                await self._mesh.async_set_white_brightness(self._mesh_id, device_brightness)
                status['white_brightness'] = device_brightness
            else:
                device_brightness = convert_value_to_available_range(kwargs[ATTR_BRIGHTNESS], 0, 255, int(0x1), int(0x64))
                await self._mesh.async_set_color_brightness(self._mesh_id, device_brightness)
                status['color_brightness'] = device_brightness

        if ATTR_COLOR_TEMP in kwargs:
            device_white_temp = convert_value_to_available_range(kwargs[ATTR_COLOR_TEMP], self.min_mireds, self.max_mireds, 0, int(0x7f))
            await self._mesh.async_set_white_temperature(self._mesh_id, device_white_temp)
            status['state'] = True
            status['white_temperature'] = device_white_temp

        if 'state' not in status:
            await self._mesh.async_on(self._mesh_id)
            status['state'] = True

        self.status_callback(status)

    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        _LOGGER.debug("[%s] turn off", self.unique_id)
        await self._mesh.async_off(self._mesh_id)
        self.status_callback({'state': False})

    @callback
    def status_callback(self, status) -> None:

        if 'state' in status:
            self._state = status['state']
        if 'white_brightness' in status:
            self._white_brightness = status['white_brightness']
        if 'white_temperature' in status:
            self._white_temperature = status['white_temperature']
        if 'color_brightness' in status:
            self._color_brightness = status['color_brightness']
        if 'red' in status:
            self._red = status['red']
        if 'green' in status:
            self._green = status['green']
        if 'blue' in status:
            self._blue = status['blue']

        if 'color_mode' in status:
            supported_color_modes = self.supported_color_modes
            color_mode = ColorMode.ONOFF
            if status['color_mode']:
                color_mode = ColorMode.RGB
            elif ColorMode.COLOR_TEMP in supported_color_modes:
                color_mode = self._attr_color_mode = ColorMode.COLOR_TEMP
            elif ColorMode.BRIGHTNESS in supported_color_modes:
                color_mode = self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_color_mode = color_mode

        _LOGGER.debug('[%s][%s] mode[%s] Status callback: %s', self.unique_id, self.name, self._attr_color_mode, status)

        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """No action here, update is handled by status_callback"""