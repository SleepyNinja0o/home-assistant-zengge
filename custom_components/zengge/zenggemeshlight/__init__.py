#!!!The majority of this code was reused from the home-assistant-awox project developed by fsaris. Huge shoutout to him for all his hard work on this!!!

from __future__ import unicode_literals

import binascii
from abc import ABC

from pygatt import BLEAddressType
from pygatt.backends.backend import DEFAULT_CONNECT_TIMEOUT_S
from pygatt.backends.gatttool.device import GATTToolBLEDevice
from pygatt.exceptions import NotificationTimeout, NotConnectedError

from . import packetutils as pckt

from os import urandom
import pygatt
import logging
import struct
import threading
import time
import subprocess

# Commands :

#: Set mesh groups.
#: Data : 3 bytes
C_MESH_GROUP = 0xd7

#: Set the mesh id. The light will still answer to the 0 mesh id. Calling the
#: command again replaces the previous mesh id.
#: Data : the new mesh id, 2 bytes in little endian order
C_MESH_ADDRESS = 0xe0

#:
C_MESH_RESET = 0xe3

#: On/Off command. Data : [0x01] and one byte 0, 1
#: Brightness command. Data : [0x02], one byte 0x1 to 0x64, and one byte for dimming target
#:   Dimming targets:
#     0x01 Set RGB and keep WC
#     0x02 Set WC, keep RGB
#     0x03 Set RGB and WC brightness
#     0x04 Set RGB and turn off WC
#     0x05 Set WC, turn off RGB
#     0x06 According to the current situation, the lights are set
#: Increasing brightness command. Data: [0x03] and one byte for brightness percentage 0x1 to 0x64 (0 or > 100, default increase by 10%)
#: Decreasing brightness command. Data: [0x04] and one byte for brightness percentage 0x1 to 0x64 (0 or > 100, default decrease by 10%)
C_POWER = 0xd0

#: Data : one byte
#SN - Not used??
#C_LIGHT_MODE = 0x33

#: Data : one byte 0 to 6
#SN - Zengge does not support presets
#C_PRESET = 0xc8

#: White temperature. one byte 0 to 0x7f
#SN - Not used by Zengge
#C_WHITE_TEMPERATURE = 0xf0

#: one byte 1 to 0x7f
#SN - Not used by Zengge
#C_WHITE_BRIGHTNESS = 0xf1

#SN - Data: 4 bytes : [Change Mode] [Value1] [Value2] [Value3]
#  Change mode of light (RGB, Warm, CCT/Lum, AuxLight, ColorTemp/Lum/AuxLight)
#    0x60 is the mode for static RGB (Value1,Value2,Value3 stand for RGB values 0-255)
#    0x61 stands for static warm white (Value1 represents warm white value 0-255)
#    0x62 stands for color temp/luminance (Value1 represents CCT scale value 0-100, Value2 represents luminance value 0-100)
#    0x63 stands for auxiliary light (Value1 represents aux light brightness)
#    0x64 stands for color temp value + aux light (Value1 represents CCT ratio value 1-100, Value 2 represents luminance value 0-100, Value 3 represents aux luminance value 0-100)
C_COLOR = 0xe2
C_COLOR_RGB = 0x60
C_COLOR_WARMWHITE = 0x61
C_COLOR_CCTLUM = 0x62
C_COLOR_AUX = 0x63
C_COLOR_CCTLUMAUX = 0x64

#: one byte : 0xa to 0x64 ....
#SN - Zengge does not use this opcode
#C_COLOR_BRIGHTNESS = 0xf2

#: Data 4 bytes : How long a color is displayed in a sequence in milliseconds as
#:   an integer in little endian order
#SN - Zengge does not use this opcode
#C_SEQUENCE_COLOR_DURATION = 0xf5

#: Data 4 bytes : Duration of the fading between colors in a sequence, in
#:   milliseconds, as an integer in little endian order
#SN - Zengge does not use this opcode
#C_SEQUENCE_FADE_DURATION = 0xf6

#: 7 bytes [Year-Low][Year-High][Month][Day][Hours][Minutes][Seconds]
C_TIME = 0xe4

#: 7 bytes [Year-Low][Year-High][Month][Day][Hours][Minutes][Seconds]
C_GET_TIME = 0xe4

#: 10 bytes
#SN - Zengge does not use this opcode
#C_ALARMS = 0xe5

#: Request current light/device status
C_GET_STATUS_SENT = 0xda

#: Response of light/device status request
C_GET_STATUS_RECEIVED = 0xdb

#: State notification
C_NOTIFICATION_RECEIVED = 0xdc

PAIR_CHAR_UUID = '00010203-0405-0607-0809-0a0b0c0d1914'
COMMAND_CHAR_UUID = '00010203-0405-0607-0809-0a0b0c0d1912'
STATUS_CHAR_UUID = '00010203-0405-0607-0809-0a0b0c0d1911'
OTA_CHAR_UUID = '00010203-0405-0607-0809-0a0b0c0d1913'

MANUFACTURER_UUID = "0000{0:x}-0000-1000-8000-00805f9b34fb".format(0x2A29)
FIRMWARE_REV_UUID = "0000{0:x}-0000-1000-8000-00805f9b34fb".format(0x2A26)
HARDWARE_REV_UUID = "0000{0:x}-0000-1000-8000-00805f9b34fb".format(0x2A27)
MODEL_NBR_UUID = "0000{0:x}-0000-1000-8000-00805f9b34fb".format(0x2A24)

logger = logging.getLogger(__name__)

class ZenggeAdapter(pygatt.GATTToolBackend):

    def connect(self, address, timeout=DEFAULT_CONNECT_TIMEOUT_S,
                address_type=BLEAddressType.public, _reconnecting=False):
        logger.info('Connecting to %s with timeout=%s', address, timeout)
        self.sendline('sec-level low')
        self._address = address
        self.__reconnecting = _reconnecting

        try:
            cmd = 'connect {0} {1}'.format(self._address, address_type.name)
            with self._receiver.event("connect", timeout):
                self.sendline(cmd)
        except NotificationTimeout:
            message = "Timed out connecting to {0} after {1} seconds.".format(
                self._address, timeout
            )
            logger.error(message)
            raise NotConnectedError(message)

        self._connected_device = ZenggeDevice(address, self)
        return self._connected_device

    def reset(self):
        # skip resetting
        return

class ZenggeDevice(GATTToolBLEDevice):

    def __init__(self, address, backend):
        super(ZenggeDevice, self).__init__(address, backend)

    def _notification_handles(self, uuid):
        # Expect notifications on the value handle...
        value_handle = self.get_handle(uuid)

        # Zengge/Eglo devices use the same handle to read/write and trigger notifications
        characteristic_config_handle = value_handle

        return value_handle, characteristic_config_handle

    @property
    def connected(self) -> bool:
        return self._connected

class ZenggeMeshLight:
    def __init__(self, mac, mesh_name="ZenggeMesh", mesh_password="ZenggeTechnology", mesh_id=0):
        """
        Args :
            mac: The light's MAC address as a string in the form AA:BB:CC:DD:EE:FF
            mesh_name: The mesh name as a string.
            mesh_password: The mesh password as a string.
            mesh_id: The mesh id (address)
        """
        self.mac = mac
        self.mesh_id = mesh_id
        self.adapter = None
        self.btdevice = None
        self.session_key = None

        self.command_char = None
        self.status_char = None

        self._reconnecting = False
        self.reconnect_counter = 0
        self.adapter = ZenggeAdapter()

        self.mesh_name = mesh_name.encode()
        self.mesh_password = mesh_password.encode()

        # Light status
        self.white_brightness = None
        self.white_temperature = None
        self.color_brightness = None
        self.red = None
        self.green = None
        self.blue = None
        self.color_mode = None
        self.transition_mode = None
        self.state = None
        self.status_callback = None

    def connect(self, mesh_name=None, mesh_password=None) -> bool:
        """
        Args :
            mesh_name: The mesh name as a string.
            mesh_password: The mesh password as a string.
        """
        if mesh_name: self.mesh_name = mesh_name.encode()
        if mesh_password: self.mesh_password = mesh_password.encode()

        assert len(self.mesh_name) <= 16, "mesh_name can hold max 16 bytes"
        assert len(self.mesh_password) <= 16, "mesh_password can hold max 16 bytes"

        self.adapter.start()
        self.btdevice = self.adapter.connect(self.mac, timeout=15)
        self.btdevice.register_disconnect_callback(self._disconnectCallback)

        session_random = urandom(8)
        message = pckt.make_pair_packet(self.mesh_name, self.mesh_password, session_random)

        logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Send pair message {message}')
        self.btdevice.char_write(PAIR_CHAR_UUID, message)

        #reply = self.btdevice.char_read_handle('1b')
        reply = bytearray(self.btdevice.char_read(PAIR_CHAR_UUID))
        #reply = self.btdevice.char_read(PAIR_CHAR_UUID)
        logger.debug(f"[{self.mesh_name.decode()}][{self.mac}] Read {reply} from characteristic {PAIR_CHAR_UUID}")

        if reply[0] == 0xd:
            self.session_key = pckt.make_session_key(self.mesh_name, self.mesh_password, session_random, reply[1:9])
        else:
            if reply[0] == 0xe:
                logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Device authentication error: known mesh credentials are not excepted by the device. Did you re-pair them to your Hao Deng app with a different account?')
            else:
                logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Unexpected pair value : {repr(reply)}')
            self.disconnect()
            return False


        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Listen for notifications')
        self.btdevice.subscribe(STATUS_CHAR_UUID, callback=self._handleNotification)

        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Send status message')
        self.btdevice.char_write(STATUS_CHAR_UUID, b'\x01')

        return True

    def _disconnectCallback(self, event):
        logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Disconnected by backend')
        if self.session_key:
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Try to reconnect...')
            reconnect_thread = threading.Thread(target=self._auto_reconnect, name='Reconnect-' + self.mac)
            reconnect_thread.start()

    def _auto_reconnect(self):
        self.session_key = None
        self.reconnect_counter = 0
        self._reconnecting = True
        while self.session_key is None and self.reconnect_counter < 3 and self._reconnecting:
            try:
                if self.reconnect():
                    break
            except Exception as err:
                self.reconnect_counter += 1
                logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Failed to reconnect attempt {self.reconnect_counter} [{type(err).__name__}] {err}')
                time.sleep(1)

        self._reconnecting = False

        logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Reconnect done after attempt {self.reconnect_counter}, success: {self.is_connected}')

        if not self.is_connected:
            self.stop()

    def setMesh(self, new_mesh_name, new_mesh_password, new_mesh_long_term_key):
        """
        Sets or changes the mesh network settings.

        Args :
            new_mesh_name: The new mesh name as a string, 16 bytes max.
            new_mesh_password: The new mesh password as a string, 16 bytes max.
            new_mesh_long_term_key: The new long term key as a string, 16 bytes max.

        Returns :
            True on success.
        """
        assert (self.session_key), "Not connected"
        assert len(new_mesh_name.encode()) <= 16, "new_mesh_name can hold max 16 bytes"
        assert len(new_mesh_password.encode()) <= 16, "new_mesh_password can hold max 16 bytes"
        assert len(new_mesh_long_term_key.encode()) <= 16, "new_mesh_long_term_key can hold max 16 bytes"

        message = pckt.encrypt(self.session_key, new_mesh_name.encode())
        message.insert(0, 0x4)
        self.btdevice.char_write(PAIR_CHAR_UUID, message, wait_for_response=True)

        message = pckt.encrypt(self.session_key, new_mesh_password.encode())
        message.insert(0, 0x5)
        self.btdevice.char_write(PAIR_CHAR_UUID, message, wait_for_response=True)

        message = pckt.encrypt(self.session_key, new_mesh_long_term_key.encode())
        message.insert(0, 0x6)
        self.btdevice.char_write(PAIR_CHAR_UUID, message, wait_for_response=True)

        time.sleep(1)
        reply = bytearray(self.btdevice.char_read(PAIR_CHAR_UUID))

        if reply[0] == 0x7:
            self.mesh_name = new_mesh_name.encode()
            self.mesh_password = new_mesh_password.encode()
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Mesh network settings accepted.')
            return True
        else:
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Mesh network settings change failed : {repr(reply)}')
            return False

    def setMeshId(self, mesh_id):
        """
        Sets the mesh id.

        Args :
            mesh_id: as a number.

        """
        data = struct.pack("<H", mesh_id)
        self.writeCommand(C_MESH_ADDRESS, data)
        self.mesh_id = mesh_id

    def writeCommand(self, command, data, dest=None, withResponse=True, attempt=0):
        """
        Args:
            command: The command, as a number.
            data: The parameters for the command, as bytes.
            dest: The destination mesh id, as a number. If None, this lightbulb's
                mesh id will be used.
        """
        assert (self.session_key)
        if dest == None: dest = self.mesh_id
        packet = pckt.make_command_packet(self.session_key, self.mac, dest, command, data)

        try:
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Writing command {command} data {repr(data)}')
            self.btdevice.char_write(uuid=COMMAND_CHAR_UUID, value=packet, wait_for_response=withResponse)
            return True
        except (NotConnectedError, NotificationTimeout) as err:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Command failed, attempt: {attempt} - [{type(err).__name__}] {err}')
            if attempt < 2:
                self.reconnect()
                return self.writeCommand(command, data, dest, withResponse, attempt+1)
            else:
                self.session_key = None
                raise err

        except Exception as err:
            logger.exception(f'[{self.mesh_name.decode()}][{self.mac}] Command failed, device is disconnected: [{type(err).__name__}] {err}', err)
            self.session_key = None
            raise err

    def resetMesh(self):
        """
        Restores the default name and password. Will disconnect the device.
        """
        return self.writeCommand(C_MESH_RESET, b'\x00')

    def readStatus(self):
        packet = self.status_char.read()
        return pckt.decrypt_packet(self.session_key, self.mac, packet)

    def _handleNotification(self, cHandle, data):

        if self.session_key is None:
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Device is disconnected, ignoring received notification [unable to decrypt without active session]')
            return

        message = pckt.decrypt_packet(self.session_key, self.mac, data)
        if message is None:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Failed to decrypt package [key: {self.session_key}, data: {data}]')
            return

        self._parseStatusResult(message)

    def _parseStatusResult(self, data): ###THIS NEEDS MODIFIED FOR ZENGGE###
        command = struct.unpack('B', data[7:8])[0]
        status = {}
        if command == C_GET_STATUS_RECEIVED:
            mode = struct.unpack('B', data[10:11])[0]
            mesh_id = (struct.unpack('B', data[4:5])[0] * 256) + struct.unpack('B', data[3:4])[0]
            white_brightness, white_temperature = struct.unpack('BB', data[11:13])
            color_brightness, red, green, blue = struct.unpack('BBBB', data[13:17])
            status = {
                'type': 'status',
                'mesh_id': mesh_id,
                'state': (mode & 1) == 1,
                'color_mode': ((mode >> 1) & 1) == 1,
                'transition_mode': ((mode >> 2) & 1) == 1,
                'red': red,
                'green': green,
                'blue': blue,
                'white_temperature': white_temperature,
                'white_brightness': white_brightness,
                'color_brightness': color_brightness,
            }

        if command == C_NOTIFICATION_RECEIVED:
            mesh_id = (struct.unpack('B', data[19:20])[0] * 256) + struct.unpack('B', data[10:11])[0]
            mode = struct.unpack('B', data[12:13])[0]
            white_brightness, white_temperature = struct.unpack('BB', data[13:15])
            color_brightness, red, green, blue = struct.unpack('BBBB', data[15:19])

            status = {
                'type': 'notification',
                'mesh_id': mesh_id,
                'state': (mode & 1) == 1,
                'color_mode': ((mode >> 1) & 1) == 1,
                'transition_mode': ((mode >> 2) & 1) == 1,
                'red': red,
                'green': green,
                'blue': blue,
                'white_temperature': white_temperature,
                'white_brightness': white_brightness,
                'color_brightness': color_brightness,
            }

        if status:
            logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Parsed status: {status}')
        else:
            logger.error(f'[{self.mesh_name.decode()}][{self.mac}] Unknown command [{command}]')

        if status and status['mesh_id'] == self.mesh_id:
            logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Update device status - mesh_id: {status["mesh_id"]}')
            self.state = status['state']
            self.color_mode = status['color_mode']
            self.transition_mode = status['transition_mode']
            self.white_brightness = status['white_brightness']
            self.white_temperature = status['white_temperature']
            self.color_brightness = status['color_brightness']
            self.red = status['red']
            self.green = status['green']
            self.blue = status['blue']

        if status and self.status_callback:
            self.status_callback(status)

    def requestStatus(self, dest=None, withResponse=False):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] requestStatus({dest})')
        data = struct.pack('B', 16)
        return self.writeCommand(C_GET_STATUS_SENT, data, dest, withResponse)

    def setColor(self, red, green, blue, dest=None):
        """
        Args :
            red, green, blue: between 0 and 0xff
        """
        data = struct.pack('BBBB', C_COLOR_RGB, red, green, blue)
        return self.writeCommand(C_COLOR, data, dest)

    def setColorBrightness(self, brightness, dest=None):
        """
        Args :
            brightness: a value between 0xa and 0x64 ...
        """
        data = struct.pack('B', brightness)
        return self.writeCommand(C_COLOR_BRIGHTNESS, data, dest)

    def setSequenceColorDuration(self, duration, dest=None):
        """
        Args :
            duration: in milliseconds.
        """
        data = struct.pack("<I", duration)
        return self.writeCommand(C_SEQUENCE_COLOR_DURATION, data, dest)

    def setSequenceFadeDuration(self, duration, dest=None):
        """
        Args:
            duration: in milliseconds.
        """
        data = struct.pack("<I", duration)
        return self.writeCommand(C_SEQUENCE_FADE_DURATION, data, dest)

    '''def setPreset(self, num, dest=None):
        """
        Set a preset color sequence.

        Args :
            num: number between 0 and 6
        """
        data = struct.pack('B', num)
        return self.writeCommand(C_PRESET, data, dest)'''

    def setWhiteBrightness(self, brightness, dest=None):
        """
        Args :
            brightness: between 1 and 0x7f
        """
        data = struct.pack('B', brightness)
        return self.writeCommand(C_WHITE_BRIGHTNESS, data, dest)

    def setWhiteTemperature(self, temp, dest=None):
        """
        Args :
            temp: between 0 and 0x7f
        """
        data = struct.pack('B', temp)
        return self.writeCommand(C_WHITE_TEMPERATURE, data, dest)

    def setWhite(self, temp, brightness, dest=None):
        """
        Args :
            temp: between 0 and 0x7f
            brightness: between 1 and 0x7f
        """
        data = struct.pack('B', temp)
        self.writeCommand(C_WHITE_TEMPERATURE, data, dest)
        data = struct.pack('B', brightness)
        return self.writeCommand(C_WHITE_BRIGHTNESS, data, dest)

    def on(self, dest=None):
        """ Turns the light on.
        """
        return self.writeCommand(C_POWER, b'\x01', dest)

    def off(self, dest=None):
        """ Turns the light off.
        """
        return self.writeCommand(C_POWER, b'\x00', dest)

    def reconnect(self) -> bool:
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Reconnecting')
        self.session_key = None
        return self.connect()

    def disconnect(self):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Disconnecting')
        self.session_key = None
        self._reconnecting = False

        try:
            self.btdevice.disconnect()
            self.adapter.stop()
        except Exception as err:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Disconnect failed: [{type(err).__name__}] {err}')
            self.stop()

    def stop(self):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Force stopping ble adapter')

        self._reconnecting = False
        self.session_key = None

        try:
            self.adapter.stop()
        except Exception as err:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Stop failed: [{type(err).__name__}] {err}')

    def getFirmwareRevision(self):
        """
        Returns :
            The firmware version as a null terminated utf-8 string.
        """
        return self.btdevice.char_read(uuid=FIRMWARE_REV_UUID)

    def getHardwareRevision(self):
        """
        Returns :
            The hardware version as a null terminated utf-8 string.
        """
        return self.btdevice.char_read(uuid=HARDWARE_REV_UUID)

    def getModelNumber(self):
        """
        Returns :
            The model as a null terminated utf-8 string.
        """
        return self.btdevice.char_read(uuid=MODEL_NBR_UUID)

    @property
    def is_connected(self) -> bool:
        return self.session_key is not None and self.btdevice and self.btdevice.connected

    @property
    def reconnecting(self) -> bool:
        return self._reconnecting