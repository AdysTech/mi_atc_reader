#!/usr/bin/env python3
import sys
import os
from dynaconf import Dynaconf
from datetime import datetime
import time
import logging
import bluetooth._bluetooth as bluez
from bluetooth_utils import (toggle_device, enable_le_scan,
                             parse_le_advertising_events,
                             disable_le_scan, raw_packet_to_str)
import struct
from dataclasses import dataclass, fields
from collections import deque
import threading
import requests
#################################


@dataclass
class SensorReading:
    temperature: float = 0
    humidity: int = 0
    voltage: float = 0
    battery: int = 0
    timestamp: int = 0
    sensor: dict = None

    def from_dict(self, d):
        if not isinstance(d, dict):
            raise ValueError(f"{type(d)} is Not a dict")
        try:
            self.temperature = d['temperature']
            self.humidity = d['humidity']
            self.voltage = d['voltage']
            self.battery = d['battery']
            self.timestamp = d['timestamp']
            self.sensor = d['sensor']
        except:
            return d  # Not a dataclass field


config = Dynaconf(
    settings_files=['config_default.yaml'],
    envvar_prefix="ATC",
)

try:
    custom_config_file = 'custom.yml'
    # Add config items from specific file
    if os.path.exists(custom_config_file):
        config.load_file(custom_config_file)
except:
    sys.stderr.write("Couldn't load config files")
    raise

numeric_level = getattr(logging, config.logging.level.upper(), None)
if not isinstance(numeric_level, int):
    raise ValueError(f'Invalid log level: {config.logging.level}')
logging.basicConfig(
    format='%(asctime)-15s %(levelname)s: %(message)s', level=numeric_level)
if numeric_level > logging.INFO and config.discovery_mode:
    print("Device discovery messages may not appear in the log in current log level. Needs to be at least at INFO")


print('using configuration')
print(config.as_dict())

dev_id = config.ble.device_id
logging.info(f'Enabling bluetooth device {dev_id}')
toggle_device(dev_id, True)

try:
    sock = bluez.hci_open_dev(dev_id)
except:
    logging.error(f"Cannot open bluetooth device {dev_id}")
    raise


def deque_thread():
    while True:
        try:
            reading = readingQueue.popleft()
            if config.influxdb.enabled:
                tags = ','.join([f"{key}={value}" for key,
                                 value in reading.sensor['tags'].items()])
                data = f"{config.influxdb.measurement},{tags},name={reading.sensor['name']},mac={reading.sensor['mac']} temperature={reading.temperature},humidity={reading.humidity},battery={reading.battery},voltage={reading.voltage} {reading.timestamp}"
                logging.debug(f'url:{influxdb_write_endpoint}, data: {data}')
                r = requests.post(influxdb_write_endpoint, data=data)
                if r.status_code != 204:
                    logging.warning(f'Failed to save{reading} due to {r.text}')
        except IndexError:
            exit_event.wait(1)
        except Exception as e:
            logging.exception(e)
        if exit_event.is_set():
            if leftout := len(readingQueue) > 0:
                logging.warning(f"Queue still has {leftout} items")
            break


exit_event = threading.Event()
readingQueue = deque()
queueThread = threading.Thread(target=deque_thread)
queueThread.start()


if config.influxdb.enabled:
    influxdb_write_endpoint = f"{config.influxdb.url}/write?db={config.influxdb.database}&precision={config.influxdb.precision}"
    logging.debug(f'writing to {influxdb_write_endpoint}')
# Set filter to "True" to see only one packet per device
logging.info('Start scanning for BLE messages')
enable_le_scan(sock, filter_duplicates=False)

try:
    def le_advertise_packet_handler(mac, adv_type, data, rssi):

        # ref: https://github.com/pvvx/ATC_MiThermometer#bluetooth-advertising-formats
        # this code will support both ATC format and pvvx's custom format.
        # https://github.com/pvvx/ATC_MiThermometer/blob/c3b89a7eddad21c054e352b64af21654d5112421/src/ble.h#L90

        pvvx = False
        data_mac = bytearray(data[5:11])
        # pvvx format sends everything in little endian format. Python assumes big endian.
        if len(data) > 18 and len(data) < 22:
            data_mac.reverse()
            pvvx = True

        # data format adds mac as part of data structure. just another check to ensure we are not listening to any other BLE on 0x181A
        if(mac.upper() != data_mac.hex(':').upper()):
            return

        # Check to make sure we are getting this message from one of ATC firmware forks.
        # both atc1441 and pvvx forks send data on GATT Service 0x181A Environmental Sensing
        if struct.unpack('<H', data[3:5])[0] != 0x181A:
            return

        reading = SensorReading(0, 0, 0, 0, int(time.time()))
        if pvvx:
            reading.temperature, reading.humidity, reading.voltage, reading.battery = struct.unpack(
                '<hHHB', data[11:18])
            reading.temperature /= 100.0
            reading.humidity /= 100.0
            reading.voltage /= 1000
        else:
            reading.temperature, reading.humidity, reading.battery, reading.voltage = struct.unpack(
                '>hBBh', data[11:17])
            reading.temperature /= 10.0
            reading.voltage /= 1000

        if config.discovery_mode:
            logging.info(
                f"Device: {mac} Temp: {reading.temperature}c Humidity: {reading.humidity}% Batt: {reading.battery}% ({reading.voltage}v)")
        if len(config.thermometers) > 0:
            if any((match := t)['mac'] == mac for t in config.thermometers):
                reading.sensor = match
                readingQueue.append(reading)

    # Blocking call (the given handler will be called each time a new LE
    # advertisement packet is detected)
    parse_le_advertising_events(sock,
                                handler=le_advertise_packet_handler,
                                debug=False)
# Scan until Ctrl-C
except KeyboardInterrupt:
    logging.info('received exit signal')
    disable_le_scan(sock)
    logging.info('waiting for background operations to complete')
    exit_event.set()
