"""Zengge connect API"""
from django.utils.encoding import force_bytes, force_str
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import binascii
import requests
import hashlib
import urllib
import json
import uuid
import time

MAGICHUE_COUNTRY_SERVERS = [{'nationName': 'Australian', 'nationCode': 'AU', 'serverApi': 'oameshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'oa.meshbroker.magichue.net'}, {'nationName': 'Avalon', 'nationCode': 'AL', 'serverApi': 'ttmeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'tt.meshbroker.magichue.net'}, {'nationName': 'China', 'nationCode': 'CN', 'serverApi': 'cnmeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'cn.meshbroker.magichue.net'}, {'nationName': 'England', 'nationCode': 'GB', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'Espana', 'nationCode': 'ES', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'France', 'nationCode': 'FR', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'Germany', 'nationCode': 'DE', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'Italy', 'nationCode': 'IT', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'Japan', 'nationCode': 'JP', 'serverApi': 'dymeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'dy.meshbroker.magichue.net'}, {'nationName': 'Russia', 'nationCode': 'RU', 'serverApi': 'eumeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'eu.meshbroker.magichue.net'}, {'nationName': 'United States', 'nationCode': 'US', 'serverApi': 'usmeshcloud.magichue.net:8081/MeshClouds/', 'brokerApi': 'us.meshbroker.magichue.net'}]
MAGICHUE_COUNTRY_SERVER = MAGICHUE_COUNTRY_SERVERS[10]['serverApi']
MAGICHUE_CONNECTURL = "http://" + MAGICHUE_COUNTRY_SERVER
MAGICHUE_NATION_DATA_ENDPOINT = "apixp/MeshData/loadNationDataNew/ZG?language=en"
MAGICHUE_USER_LOGIN_ENDPOINT = "apixp/User001/LoginForUser/ZG"
MAGICHUE_GET_MESH_ENDPOINT = 'apixp/MeshData/GetMyMeshPlaceItems/ZG?userId='
MAGICHUE_GET_MESH_DEVICES_ENDPOINT = 'apixp/MeshData/GetMyMeshDeviceItems/ZG?placeUniID=&userId='


class ZenggeConnect:

    def __init__(self, username: str, password: str, installation_id: str = None):
        self._username = username
        self._password = password
        self._md5password = hashlib.md5(password.encode()).hexdigest()

        self._user_id = None
        self._auth_token = None
        self._device_secret = None
        self._mesh = None
        self._installation_id = installation_id

        if not self._installation_id:
            self._installation_id = str(uuid.uuid4())

        self.login()
        #self.credentials()

    def generate_timestampcheckcode(self):
        SECRET_KEY = "0FC154F9C01DFA9656524A0EFABC994F"
        timestamp = str(int(time.time()*1000))
        value = force_bytes("ZG" + timestamp)
        backend = default_backend()
        key = force_bytes(SECRET_KEY)
        encryptor = Cipher(algorithms.AES(key), modes.ECB(), backend).encryptor()
        padder = padding.PKCS7(algorithms.AES(key).block_size).padder()
        padded_data = padder.update(value) + padder.finalize()
        encrypted_text = encryptor.update(padded_data) + encryptor.finalize()
        checkcode = binascii.hexlify(encrypted_text).decode()
        return timestamp,checkcode

    def login(self):
        timestampcheckcode = self.generate_timestampcheckcode()
        timestamp = timestampcheckcode[0]
        checkcode = timestampcheckcode[1]
        payload = dict(userID=self._username, password=self._md5password, appSys='Android', timestamp=timestamp, appVer='', checkcode=checkcode)

        headers = {
            'User-Agent': 'HaoDeng/1.5.7(ANDROID,10,en-US)',
            'Accept-Language': 'en-US',
            'Accept': 'application/json',
            'token': '',
            'Content-Type': 'application/json',
            'Accept-Encoding': 'gzip'
        }

        response = requests.post(MAGICHUE_CONNECTURL + MAGICHUE_USER_LOGIN_ENDPOINT, headers=headers, json=payload)

        if response.status_code != 200:
            raise Exception('Login failed - %s' % response.json()['error'])

        responseJSON = response.json()['result']
        self._user_id = responseJSON['userId']
        self._auth_token = responseJSON['auth_token']
        self._device_secret = responseJSON['deviceSecret']

    def credentials(self):
        if self._auth_token is not None and self._user_id is not None:
            headers = {
                'User-Agent': 'HaoDeng/1.5.7(ANDROID,10,en-US)',
                'Accept-Language': 'en-US',
                'Accept': 'application/json',
                'token': self._auth_token,
                'Content-Type': 'application/json',
                'Accept-Encoding': 'gzip'
            }

            response = requests.get(MAGICHUE_CONNECTURL + MAGICHUE_GET_MESH_ENDPOINT + urllib.parse.quote_plus(self._user_id), headers=headers)
            if response.status_code != 200:
                raise Exception('Loading data failed - %s' % response.json()['error'])
            self._mesh = response.json()['result'][0]
            return self._mesh
        else:
            raise Exception('No login session detected! - %s' % response.json()['error'])

    def devices(self):
        if self._auth_token is not None and self._user_id is not None:
            headers = {
                'User-Agent': 'HaoDeng/1.5.7(ANDROID,10,en-US)',
                'Accept-Language': 'en-US',
                'Accept': 'application/json',
                'token': self._auth_token,
                'Content-Type': 'application/json',
                'Accept-Encoding': 'gzip'
            }

            placeUniID = self._mesh['placeUniID']
            MAGICHUE_GET_MESH_DEVICES_ENDPOINTNEW = MAGICHUE_GET_MESH_DEVICES_ENDPOINT.replace("placeUniID=","placeUniID=" + placeUniID)
            MAGICHUE_GET_MESH_DEVICES_ENDPOINTNEW = MAGICHUE_GET_MESH_DEVICES_ENDPOINTNEW.replace("userId=","userId="+urllib.parse.quote_plus(self._user_id))
            response = requests.get(MAGICHUE_CONNECTURL + MAGICHUE_GET_MESH_DEVICES_ENDPOINTNEW, headers=headers)
            
            if response.status_code != 200:
                raise Exception('Device retrieval for mesh failed - %s' % response.json()['error'])
            else:
                responseJSON = response.json()['result']
                self._mesh.update({'devices':responseJSON})
                return responseJSON
        else:
            raise Exception('No login session detected! - %s' % response.json()['error'])