#!!!The majority of this code was reused from the home-assistant-awox project developed by fsaris. Huge shoutout to him for all his hard work on this!!!

from __future__ import unicode_literals

#import binascii
#from abc import ABC

#from pygatt import BLEAddressType
#from pygatt.backends.backend import DEFAULT_CONNECT_TIMEOUT_S
#from pygatt.backends.gatttool.device import GATTToolBLEDevice
#from pygatt.exceptions import NotificationTimeout, NotConnectedError
from bleak import BleakClient #, BleakScanner

from . import packetutils as pckt

from os import urandom
import asyncio
import logging
import struct
import threading
import math

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
C_WHITE_TEMPERATURE = 0xe2

#: one byte 1 to 0x7f
#SN - Not used by Zengge
C_WHITE_BRIGHTNESS = 0xd0

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
C_COLOR_BRIGHTNESS = 0xd0

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


def decode_color(color):
	return hsl_to_rgb(h255_to_h360(color))

class ZenggeMeshLight:
    def __init__(self, mac, mesh_name="ZenggeMesh", mesh_password="ZenggeTechnology", mesh_id=0x0211):
        """
        Args :
            mac: The light's MAC address as a string in the form AA:BB:CC:DD:EE:FF
            mesh_name: The mesh name as a string.
            mesh_password: The mesh password as a string.
            mesh_id: The mesh id (address)
        """
        self.mac = mac
        self.mesh_id = mesh_id
        self.client = None
        self.session_key = None

        self.command_char = None
        self.status_char = None

        self._reconnecting = False
        self.reconnect_counter = 0

        self.mesh_name = mesh_name.encode()
        self.mesh_password = mesh_password.encode()

        # Light status
        self.white_brightness = 1
        self.white_temperature = 1
        self.color_brightness = 1
        self.red = 0
        self.green = 0
        self.blue = 0
        self.color_mode = False
        self.state = False
        self.status_callback = None

    async def enable_notify(self): #Huge thanks to 'cocoto' for helping me figure out this issue with Zengge!!
        await self.send_packet(0x01,bytes([]),self.mesh_id,uuid=STATUS_CHAR_UUID)
        print("Enable notify packet sent...")
        await self.client.start_notify(STATUS_CHAR_UUID, self._handleNotification)

    async def mesh_login(self):
        if self.client == None:
            return
        session_random = urandom(8)
        message = pckt.make_pair_packet(self.mesh_name.encode(), self.mesh_pass.encode(), session_random)
        logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Send pair message {message}')
        pairReply = await self.client.write_gatt_char(PAIR_CHAR_UUID, bytes(message), True)
        await asyncio.sleep(0.3)
        reply = await self.client.read_gatt_char(PAIR_CHAR_UUID)
        logger.debug(f"[{self.mesh_name.decode()}][{self.mac}] Read {reply} from characteristic {PAIR_CHAR_UUID}")

        self.session_key = pckt.make_session_key(self.mesh_name.encode(), self.mesh_pass.encode(), session_random, reply[1:9])
        if reply[0] == 0xd:
            self.session_key = pckt.make_session_key(self.mesh_name, self.mesh_password, session_random, reply[1:9])
        else:
            if reply[0] == 0xe:
                logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Device authentication error: known mesh credentials are not excepted by the device. Did you re-pair them to your Hao Deng app with a different account?')
            else:
                logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Unexpected pair value : {repr(reply)}')
            self.disconnect()
            return False

    async def send_packet(self, command, data, dest=None, withResponse=True, attempt=0, uuid=COMMAND_CHAR_UUID):
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
            print(f'[{self.mesh_name}][{self.mac}] Writing command {command} data {repr(data)}')
            return await self.client.write_gatt_char(uuid, packet)
        except Exception as err:
            print(f'[{self.mesh_name}][{self.mac}] Command failed, attempt: {attempt} - [{type(err).__name__}] {err}')
            if attempt < 2:
                self.connect()
                return self.send_packet(command, data, dest, withResponse, attempt+1)
            else:
                self.session_key = None
                raise err

    async def connect(self, mesh_name=None, mesh_password=None) -> bool:
        """
        Args :
            mesh_name: The mesh name as a string.
            mesh_password: The mesh password as a string.
        """
        if mesh_name: self.mesh_name = mesh_name.encode()
        if mesh_password: self.mesh_password = mesh_password.encode()

        assert len(self.mesh_name) <= 16, "mesh_name can hold max 16 bytes"
        assert len(self.mesh_password) <= 16, "mesh_password can hold max 16 bytes"

        self.client = BleakClient(self.mac, timeout=15, disconnected_callback=self._disconnectCallback)
        await self.client.connect()
        self.mesh_login()

        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Listen for notifications')
        self.client.start_notify()

        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Send status message')
        self.client.write_gatt_char(STATUS_CHAR_UUID, b'\x01')
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
                asyncio.sleep(1)

        self._reconnecting = False

        logger.info(f'[{self.mesh_name.decode()}][{self.mac}] Reconnect done after attempt {self.reconnect_counter}, success: {self.is_connected}')

        if not self.is_connected:
            self.stop()

    async def setMesh(self, new_mesh_name, new_mesh_password, new_mesh_long_term_key):
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
        if self.session_key is None:
            print("BLE device is not connected!")
            self.mac = input('Please enter MAC of device:')
            self.connect()
        message = pckt.encrypt(self.session_key, new_mesh_name.encode())
        message.insert(0, 0x4)
        await self.client.write_gatt_char(PAIR_CHAR_UUID, message)
        message = pckt.encrypt(self.session_key, new_mesh_password.encode())
        message.insert(0, 0x5)
        await self.client.write_gatt_char(PAIR_CHAR_UUID, message)
        message = pckt.encrypt(self.session_key, new_mesh_long_term_key.encode())
        message.insert(0, 0x6)
        await self.client.write_gatt_char(PAIR_CHAR_UUID, message)
        asyncio.sleep(1)
        reply = bytearray(await self.client.read_gatt_char(PAIR_CHAR_UUID))
        if reply[0] == 0x7:
            self.mesh_name = new_mesh_name
            self.mesh_pass = new_mesh_password
            print(f'[{self.mesh_name}]-[{self.mesh_pass}]-[{self.mac}] Mesh network settings accepted.')
            return True
        else:
            print(f'[{self.mesh_name}][{self.mac}] Mesh network settings change failed : {repr(reply)}')
            return False

    def setMeshId(self, mesh_id):
        """
        Sets the mesh id.

        Args :
            mesh_id: as a number.

        """
        data = struct.pack("<H", mesh_id)
        self.send_packet(C_MESH_ADDRESS, data)
        self.mesh_id = mesh_id

    def resetMesh(self):
        """
        Restores the default name and password. Will disconnect the device.
        """
        return self.send_packet(C_MESH_RESET, b'\x00')

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
        if command == C_GET_STATUS_RECEIVED: #This does not return anything useful other than device is online/talking to mesh
            mesh_id = struct.unpack('B', data[3:4])[0]
            status = {
                'type': 'status',
                'mesh_id': mesh_id,
                'state': self.state,
                'color_mode': self.color_mode,
                'red': self.red,
                'green': self.green,
                'blue': self.blue,
                'white_temperature': self.white_temperature,
                'white_brightness': self.white_brightness,
                'color_brightness': self.color_brightness,
            }

        if command == C_NOTIFICATION_RECEIVED:
            mesh_id = struct.unpack('B', data[10:11])[0] #Device ID should only be data[10:11]
            mode = struct.unpack('B', data[13:14])[0] #Mode is [13:14][0]
            white_brightness = struct.unpack('B', data[12:13])[0] #should be [12:13][0]
            white_temperature = color = struct.unpack('B', data[14:15])[0] #should be [12:13][0]
            color_brightness = white_brightness

            if(mode == 63 or mode == 42):
                color_mode = 'rgb'
                red, green, blue = hsl_to_rgb(h255_to_h360(color)) #Converts from 1 value(kelvin) to RGB

            status = {
                'type': 'notification',
                'mesh_id': mesh_id,
                'state': white_brightness != 0,
                'color_mode': color_mode,
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
            self.white_brightness = status['white_brightness']
            self.white_temperature = status['white_temperature']
            self.color_brightness = status['color_brightness']
            self.red = status['red']
            self.green = status['green']
            self.blue = status['blue']

        if status and self.status_callback:
            self.status_callback(status)

    def requestStatus(self, dest=0xffff, withResponse=False):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] requestStatus({dest})')
        return self.client.write_gatt_char(STATUS_CHAR_UUID, b'\x01') #Zengge can't use Status request to receive device details, need notification request

    def setColor(self, red, green, blue, dest=None):
        """
        Args :
            red, green, blue: between 0 and 0xff
        """
        data = struct.pack('BBBB', C_COLOR_RGB, red, green, blue)
        return self.send_packet(C_COLOR, data, dest)

    def setColorBrightness(self, brightness, dest=None):
        """
        Args :
            brightness: a value between 0xa and 0x64 ...
        """
        data = struct.pack('BBB', 0x02 , brightness, 0x06)
        return self.send_packet(C_COLOR_BRIGHTNESS, data, dest)

    def setSequenceColorDuration(self, duration, dest=None):
        """
        Args :
            duration: in milliseconds.
        """
        data = struct.pack("<I", duration)
        return False #return self.send_packet(C_SEQUENCE_COLOR_DURATION, data, dest)

    def setSequenceFadeDuration(self, duration, dest=None):
        """
        Args:
            duration: in milliseconds.
        """
        data = struct.pack("<I", duration)
        return False #return self.send_packet(C_SEQUENCE_FADE_DURATION, data, dest)

    def setWhiteBrightness(self, brightness, dest=None):
        """
        Args :
            brightness: between 1 and 0x7f
        """
        data = struct.pack('BBB', 0x02 , brightness, 0x06)
        return self.send_packet(C_WHITE_BRIGHTNESS, data, dest)

    def setWhiteTemperature(self, temp, dest=None):
        """
        Args :
            temp: between 0 and 0x64
        """
        data = struct.pack('BBB', 0x62 , temp, self.white_brightness)
        return self.send_packet(C_WHITE_TEMPERATURE, data, dest)

    def setWhite(self, temp, brightness, dest=None):
        """
        Args :
            temp: between 0 and 0x7f
            brightness: between 1 and 0x7f
        """
        data = struct.pack('B', temp)
        self.send_packet(C_WHITE_TEMPERATURE, data, dest)
        data = struct.pack('BB', 0x02 , brightness)
        return self.send_packet(C_WHITE_BRIGHTNESS, data, dest)

    def on(self, dest=None):
        """ Turns the light on.
        """
        return self.send_packet(C_POWER, b'\x01', dest)

    def off(self, dest=None):
        """ Turns the light off.
        """
        return self.send_packet(C_POWER, b'\x00', dest)

    async def reconnect(self) -> bool:
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Reconnecting')
        self.session_key = None
        return await self.connect()

    def disconnect(self):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Disconnecting')
        self.session_key = None
        self._reconnecting = False

        try:
            self.client.disconnect()
        except Exception as err:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Disconnect failed: [{type(err).__name__}] {err}')
            self.stop()

    def stop(self):
        logger.debug(f'[{self.mesh_name.decode()}][{self.mac}] Force stopping ble adapter')

        self._reconnecting = False
        self.session_key = None

        try:
            self.client.disconnect()
        except Exception as err:
            logger.warning(f'[{self.mesh_name.decode()}][{self.mac}] Stop failed: [{type(err).__name__}] {err}')

    def getFirmwareRevision(self):
        """
        Returns :
            The firmware version as a null terminated utf-8 string.
        """
        return self.client.read_gatt_char(FIRMWARE_REV_UUID)

    def getHardwareRevision(self):
        """
        Returns :
            The hardware version as a null terminated utf-8 string.
        """
        return self.client.read_gatt_char(HARDWARE_REV_UUID)

    def getModelNumber(self):
        """
        Returns :
            The model as a null terminated utf-8 string.
        """
        return self.client.read_gatt_char(MODEL_NBR_UUID)

    @property
    def is_connected(self) -> bool:
        return self.session_key is not None and self.btdevice and self.btdevice.connected

    @property
    def reconnecting(self) -> bool:
        return self._reconnecting