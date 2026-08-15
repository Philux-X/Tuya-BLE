"""The Tuya BLE integration."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from struct import pack
from threading import Timer
import time
from typing import Any, Callable

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
    LockState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


_LOGGER = logging.getLogger(__name__)


TuyaBLELockIsAvailable = Callable[
    ["TuyaBLELock", TuyaBLEProductInfo], bool
] | None


@dataclass
class TuyaBLELockMapping:
    dp_id: int
    dp_id_lock: int | None
    dp_id_unlock: int | None
    dp_id_nop: int | None = None
    keep_connect_timer: int = 60
    description: LockEntityDescription = field(
        default_factory=lambda: LockEntityDescription(
            key="manual_lock",
        )
    )
    force_add: bool = True
    keep_connect: bool = False
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLELockIsAvailable = None


@dataclass
class TuyaBLECategoryLockMapping:
    products: dict[str, list[TuyaBLELockMapping]] | None = None
    mapping: list[TuyaBLELockMapping] | None = None


mapping: dict[str, TuyaBLECategoryLockMapping] = {
    "jtmspro": TuyaBLECategoryLockMapping(
        products={
            "rlyxv7pe": [  # Gimdow Smart Lock
                TuyaBLELockMapping(
                    dp_id_unlock=6,
                    dp_id_lock=46,
                    dp_id=47,
                    dp_id_nop=52,
                    keep_connect=True,
                    keep_connect_timer=60,
                    description=LockEntityDescription(
                        key="manual_lock",
                    ),
                ),
            ],

            "hc7n0urm": [  # Raykube A1 Ultra / A1 Pro Max
                TuyaBLELockMapping(
                    dp_id_unlock=6,
                    dp_id_lock=46,
                    dp_id=118,
                    dp_id_nop=52,
                    keep_connect=False,
                    keep_connect_timer=60,
                    description=LockEntityDescription(
                        key="manual_lock",
                    ),
                ),
            ],

            "hhxgpozj": [  # YR05 / H13 new-chip lock
                TuyaBLELockMapping(
                    # Confirmed:
                    # DP47 False = locked
                    # DP47 True  = unlocked
                    dp_id=47,

                    # Confirmed local lock command.
                    dp_id_lock=46,

                    # Authenticated BLE unlock command.
                    dp_id_unlock=71,

                    keep_connect=False,
                    keep_connect_timer=60,
                    description=LockEntityDescription(
                        key="manual_lock",
                        name="Lock Control",
                    ),
                ),
            ],
        }
    ),
}


def get_mapping_by_device(
    device: TuyaBLEDevice,
) -> list[TuyaBLELockMapping]:
    category = mapping.get(device.category)

    if category is None:
        return []

    if category.products is not None:
        product_mapping = category.products.get(device.product_id)

        if product_mapping is not None:
            return product_mapping

    if category.mapping is not None:
        return category.mapping

    return []


class TuyaBLELock(TuyaBLEEntity, LockEntity):
    """Representation of a Tuya BLE lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLELockMapping,
    ) -> None:
        super().__init__(
            hass,
            coordinator,
            device,
            product,
            mapping.description,
        )

        self._mapping = mapping

        self._current_state = STATE_UNKNOWN
        self._target_state = None

        self._commanded = False
        self._commanded_timer: datetime | None = None

        self._datapoint_nop = None
        self._isjammed = False

        # YR05 authenticated-unlock tracking.
        self._yr05_unlock_request: bytes | None = None
        self._yr05_unlock_accepted: bool | None = None

        self._update_attrs()

        if mapping.keep_connect and mapping.dp_id_nop is not None:
            self._thread = Timer(
                self._mapping.keep_connect_timer,
                self.send_nop_request,
            )
            self._thread.start()

            self._datapoint_nop = device.datapoints.get_or_create(
                self._mapping.dp_id_nop,
                TuyaBLEDataPointType.DT_BOOL,
                False,
            )


    def send_nop_request(self) -> None:
        """Send periodic keepalive request."""
        while True:
            if self._datapoint_nop:
                self._hass.create_task(
                    self._datapoint_nop.set_value(True)
                )

            time.sleep(self._mapping.keep_connect_timer)


    def _build_yr05_unlock_payload(self) -> bytes:
        """Build authenticated DP71 unlock request for the YR05."""

        encoded = self._device.ble_unlock_check

        if not encoded:
            raise HomeAssistantError(
                "YR05 ble_unlock_check is missing from devices.json."
            )

        try:
            reported = base64.b64decode(encoded)
        except Exception as ex:
            raise HomeAssistantError(
                "YR05 ble_unlock_check is not valid Base64."
            ) from ex

        if len(reported) != 19:
            raise HomeAssistantError(
                f"YR05 ble_unlock_check must decode to 19 bytes; "
                f"got {len(reported)}."
            )

        # Stored DP71 value is a response/report:
        #
        #   peripheral_id : 2
        #   central_id    : 2
        #   random        : 8
        #   operation     : 1
        #   timestamp     : 4
        #   method        : 1
        #   result        : 1
        #
        # Command reverses the first two IDs:
        #
        #   central_id
        #   peripheral_id
        #   random
        #   operation
        #   timestamp
        #   method
        #   unlock_info

        peripheral_id = reported[0:2]
        central_id = reported[2:4]
        random_number = reported[4:12]

        operation_unlock = b"\x01"
        timestamp = pack(">I", int(time.time()))
        unlock_method_mobile = b"\x00"

        # Official Smart Life capture used value 1 here.
        unlock_info = b"\x01"

        payload = (
            central_id
            + peripheral_id
            + random_number
            + operation_unlock
            + timestamp
            + unlock_method_mobile
            + unlock_info
        )

        _LOGGER.debug("YR05 DP71 authenticated unlock request prepared")

        return payload


    def _process_yr05_unlock_response(self) -> None:
        """Validate and process the YR05 DP71 unlock response."""

        if self._yr05_unlock_request is None:
            return

        datapoint = self._device.datapoints[71]

        if not datapoint:
            return

        response = datapoint.value

        if not isinstance(response, (bytes, bytearray)):
            return

        response = bytes(response)

        if len(response) != 19:
            return

        request = self._yr05_unlock_request

        # Request:
        #
        # central_id     [0:2]
        # peripheral_id  [2:4]
        # random         [4:12]
        # operation      [12]
        # timestamp      [13:17]
        # method         [17]
        # unlock_info    [18]
        #
        # Response reverses central/peripheral IDs and replaces
        # unlock_info with the result code.

        matching_response = (
            response[0:2] == request[2:4]
            and response[2:4] == request[0:2]
            and response[4:18] == request[4:18]
        )

        if not matching_response:
            return

        result = response[18]

        if result == 0:
            if self._yr05_unlock_accepted is not True:
                _LOGGER.debug(
                    "YR05 authenticated unlock accepted by lock "
                    "(DP71 result=0x00)"
                )

            self._yr05_unlock_accepted = True

        else:
            _LOGGER.warning(
                "YR05 authenticated unlock rejected by lock "
                "(DP71 result=0x%02x)",
                result,
            )

            self._yr05_unlock_accepted = False
            self._commanded = False
            self._isjammed = True
            self._target_state = None

            # Clear the request so a repeated coordinator update
            # cannot process the same failure again.
            self._yr05_unlock_request = None


    @property
    def is_locked(self) -> bool | None:
        """Return True if device is locked."""
        if self._current_state == STATE_UNKNOWN:
            return None

        return self._current_state == LockState.LOCKED


    @property
    def is_locking(self) -> bool:
        """Return True if device is locking."""
        return (
            self._current_state == LockState.UNLOCKED
            and self._target_state == LockState.LOCKED
            and self._commanded
        )


    @property
    def is_unlocking(self) -> bool:
        """Return True if device is unlocking."""
        return (
            self._current_state == LockState.LOCKED
            and self._target_state == LockState.UNLOCKED
            and self._commanded
        )


    @property
    def is_jammed(self) -> bool | None:
        """Return True if device is jammed."""
        return self._isjammed


    @property
    def should_poll(self) -> bool:
        return False


    def _update_attrs(self) -> None:
        """Update Home Assistant lock attributes."""

        locked = self.is_locked

        self._attr_is_locking = self.is_locking
        self._attr_is_unlocking = self.is_unlocking
        self._attr_is_locked = locked

        if locked is None:
            self._attr_is_unlocked = None
        else:
            self._attr_is_unlocked = not locked

        self._attr_is_jammed = self.is_jammed
        self._attr_changed_by = super().changed_by


    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device."""
        await self._set_lock_state(LockState.LOCKED)


    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device."""
        await self._set_lock_state(LockState.UNLOCKED)


    async def _set_lock_state(self, state: str) -> None:
        """Send lock or unlock command."""

        self._target_state = state

        self._update_attrs()
        self.async_write_ha_state()

        #
        # YR05 authenticated unlock
        #
        if (
            self._device.product_id == "hhxgpozj"
            and state == LockState.UNLOCKED
        ):
            payload = self._build_yr05_unlock_payload()

            # Store exact request so the DP71 response must match
            # this transaction before we accept it.
            self._yr05_unlock_request = payload
            self._yr05_unlock_accepted = None

            datapoint = self._device.datapoints.get_or_create(
                71,
                TuyaBLEDataPointType.DT_RAW,
                payload,
            )

            await datapoint.set_value(payload)

            self._commanded = True
            self._commanded_timer = datetime.now()
            self._isjammed = False

            _LOGGER.debug(
                "hhxgpozj: Sent authenticated unlock using DP71"
            )

            return

        #
        # Normal BOOL lock/unlock path
        #
        if state == LockState.UNLOCKED:
            dp_id = self._mapping.dp_id_unlock
        else:
            dp_id = self._mapping.dp_id_lock

        if dp_id is None:
            raise HomeAssistantError(
                f"No datapoint mapped for requested lock state: {state}"
            )

        datapoint = self._device.datapoints.get_or_create(
            dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )

        # Existing Raykube behavior.
        if self._device.product_id == "hc7n0urm":
            await datapoint.set_value(True)

            self._current_state = state
            self._commanded = False
            self._isjammed = False

            self._update_attrs()
            self.async_write_ha_state()
            return

        # Gimdow / YR05 locking.
        await datapoint.set_value(True)

        self._commanded = True
        self._commanded_timer = datetime.now()
        self._isjammed = False

        _LOGGER.debug(
            "%s: Sent lock command using DP %s",
            self._device.product_id,
            dp_id,
        )


    def update_device_state(self) -> None:
        """Update state from lock datapoints."""

        #
        # First inspect DP71 if this is an active YR05 unlock.
        #
        if (
            self._device.product_id == "hhxgpozj"
            and self._target_state == LockState.UNLOCKED
            and self._commanded
        ):
            self._process_yr05_unlock_response()

        #
        # DP47 is the physical cylinder state on the YR05.
        #
        datapoint = self._device.datapoints[self._mapping.dp_id]

        if datapoint:
            if datapoint.value:
                self._current_state = LockState.UNLOCKED
            else:
                self._current_state = LockState.LOCKED

        #
        # Command completion / timeout.
        #
        if self._commanded:
            if self._current_state == self._target_state:
                #
                # Physical state confirms success.
                #
                self._commanded = False
                self._isjammed = False

                if (
                    self._device.product_id == "hhxgpozj"
                    and self._target_state == LockState.UNLOCKED
                ):
                    _LOGGER.debug(
                        "YR05 physical unlock confirmed by DP47"
                    )

                self._yr05_unlock_request = None

            elif (
                self._commanded_timer is not None
                and datetime.now()
                > self._commanded_timer + timedelta(seconds=12)
            ):
                #
                # Command was accepted/sent but physical state never
                # reached the requested target.
                #
                self._isjammed = True
                self._commanded = False

                if (
                    self._device.product_id == "hhxgpozj"
                    and self._target_state == LockState.UNLOCKED
                ):
                    if self._yr05_unlock_accepted is True:
                        _LOGGER.warning(
                            "YR05 DP71 unlock was accepted, but "
                            "DP47 never changed to unlocked"
                        )
                    else:
                        _LOGGER.warning(
                            "YR05 unlock timed out before physical "
                            "unlock confirmation"
                        )

                self._yr05_unlock_request = None


    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle BLE datapoint update."""

        self.update_device_state()
        self._update_attrs()
        self.async_write_ha_state()


    @property
    def available(self) -> bool:
        """Return if entity is available."""

        if self._device.product_id == "hc7n0urm":
            return True

        result = super().available

        if result and self._mapping.is_available:
            result = self._mapping.is_available(
                self,
                self._product,
            )

        return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE locks."""

    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]

    mappings = get_mapping_by_device(data.device)

    entities: list[TuyaBLELock] = []

    for mapping in mappings:
        if (
            mapping.force_add
            or data.device.datapoints.has_id(
                mapping.dp_id,
                mapping.dp_type,
            )
        ):
            entities.append(
                TuyaBLELock(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )

    async_add_entities(entities)