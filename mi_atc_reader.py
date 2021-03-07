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
import signal
#################################


@dataclass
class SensorReading:
    temperature: float = 0
    humidity: int = 0
    voltage: float = 0
    battery: int = 0
    timestamp: int = 0
    sensor: dict = None
    counter: int = 0

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


def le_advertise_packet_handler(mac, adv_type, data, rssi):
    # Check to make sure we are getting this message from one of ATC firmware forks.
    # both atc1441 and pvvx forks send data on GATT Service 0x181A Environmental Sensing
    if struct.unpack('<H', data[3:5])[0] != 0x181A:
        return

    # data format adds mac as part of data structure. just another check to ensure we are not listening to any other BLE on 0x181A
    data_mac, pvvx = get_data_mac(data)
    if(mac.upper() != data_mac.hex(':').upper()):
        return

    reading = parse_pvvx_format(data) if pvvx else parse_atc_format(data)
    if reading == None:
        return

    if config.discovery_mode:
        logging.info(
            f"Device: {mac} Temp: {reading.temperature}c Humidity: {reading.humidity}% Batt: {reading.battery}% ({reading.voltage}v)")

    if len(config.thermometers) > 0:
        if any((match := t)['mac'] == mac for t in config.thermometers):
            reading.sensor = match
            readingQueue.append(reading)


def handle_retry(reading: SensorReading):
    if len(readingQueue) < config.errorbuffer.max_items:
        readingQueue.appendleft(reading)
    else:
        logging.warning(f'retry queue full, Discoreded Message: {reading}!')
    exit_event.wait(5)


def deque_thread():
    while True:
        try:
            logging.debug(f"Reading Queue Depth: {len(readingQueue)}")
            reading = readingQueue.popleft()
            logging.debug(reading)
            if config.influxdb.enabled:
                tags = ','.join([f"{key}={value}" for key,
                                 value in reading.sensor['tags'].items()])
                payload = f"{config.influxdb.measurement},{tags},name={reading.sensor['name']},mac={reading.sensor['mac']} temperature={reading.temperature},humidity={reading.humidity},battery={reading.battery},voltage={reading.voltage} {reading.timestamp}"
                try:
                    r = requests.post(influxdb_write_endpoint,
                                      data=payload, timeout=1)
                    # ', return:{r.status_code}')
                    logging.debug(
                        f'url:{influxdb_write_endpoint}, data: {payload}')
                    if r.status_code != 204:
                        if r.status_code == 404:
                            logging.warning(
                                f'Influxdb database missing!!.. manually create the DB.')
                            handle_retry(reading)
                        else:
                            logging.warning(
                                f'Failed to save{reading} due to {r.text}')
                except requests.Timeout:
                    logging.warning(f'Influxdb Timeout.. retrying')
                    handle_retry(reading)
                except requests.ConnectionError:
                    logging.warning(
                        f'Unable to connect to InfluxDB.. retrying')
                    handle_retry(reading)
        except IndexError:
            exit_event.wait(1)
        except Exception as e:
            logging.exception(e)
        if exit_event.is_set():
            if len(readingQueue) > 0:
                logging.warning(f"Queue had {len(readingQueue)} unsaved items")
            break


# ref: https://github.com/pvvx/ATC_MiThermometer#bluetooth-advertising-formats
# this code will support both ATC format and pvvx's custom format.
# https://github.com/pvvx/ATC_MiThermometer/blob/c3b89a7eddad21c054e352b64af21654d5112421/src/ble.h#L90

def get_data_mac(data):
    pvvx = False
    data_mac = bytearray(data[5:11])
    # pvvx format sends everything in little endian format. Python assumes big endian.
    if len(data) > 18 and len(data) < 22:
        data_mac.reverse()
        pvvx = True
    return data_mac, pvvx


def parse_atc_format(data) -> SensorReading:
    try:
        reading = SensorReading(0, 0, 0, 0, int(time.time()))
        reading.temperature, reading.humidity, reading.battery, reading.voltage, reading.counter = struct.unpack(
            '>hBBhB', data[11:18])
        reading.temperature /= 10.0
        reading.voltage /= 1000
        return reading
    except Exception as e:
        logging.exception(e)
        return None


def parse_pvvx_format(data) -> SensorReading:
    try:
        reading = SensorReading(0, 0, 0, 0, int(time.time()))
        reading.temperature, reading.humidity, reading.voltage, reading.battery, reading.counter = struct.unpack(
            '<hHHBB', data[11:19])
        reading.temperature /= 100.0
        reading.humidity /= 100.0
        reading.voltage /= 1000
        return reading
    except Exception as e:
        logging.exception(e)
        return None


def loadConfig() -> Dynaconf:
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
    return config


def exit_gracefully(signum, stack_frame):
    # Raises SystemExit(0):
    logging.log(level=log_level, msg=f'received signal{signum}')
    exit_event.set()
    sys.exit(0)


if __name__ == '__main__':  # has a blocking call at the end
    signal.signal(signal.SIGINT, exit_gracefully)
    signal.signal(signal.SIGTERM, exit_gracefully)
    config = loadConfig()
    log_level = getattr(logging, config.logging.level.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {config.logging.level}')
    logging.basicConfig(level=log_level,
                        format='%(asctime)-15s %(levelname)s: %(message)s')
    logging.log(level=log_level, msg='using configuration')
    logging.log(level=log_level, msg=f'{config.as_dict()}')
    logging.log(level=log_level,
                msg=f'Current log level: {logging.getLevelName(logging.getLogger().getEffectiveLevel())}')

    if log_level > logging.INFO and config.discovery_mode:
        logging.log(
            level=log_level, msg="Device discovery messages may not appear in the log in current log level. Needs to be at least at INFO")

    dev_id = config.ble.device_id
    logging.info(f'Enabling bluetooth device {dev_id}')
    toggle_device(dev_id, True)

    try:
        sock = bluez.hci_open_dev(dev_id)
    except:
        logging.error(f"Cannot open bluetooth device {dev_id}")
        raise

    if config.influxdb.enabled:
        influxdb_write_endpoint = f"{config.influxdb.url}/write?db={config.influxdb.database}&precision={config.influxdb.precision}"
        logging.info(f'writing to {influxdb_write_endpoint}')
    # Set filter to "True" to see only one packet per device
    logging.info('Start scanning for BLE messages')
    enable_le_scan(sock, filter_duplicates=False)

    exit_event = threading.Event()
    readingQueue = deque()
    queueThread = threading.Thread(target=deque_thread)
    #queueThread.daemon = True
    queueThread.start()

    try:
        # Blocking call (the given handler will be called each time a new LE
        # advertisement packet is detected)
        parse_le_advertising_events(sock,
                                    handler=le_advertise_packet_handler,
                                    debug=config.ble.debug, control_event=exit_event)
    # Scan until Ctrl-C
    except (KeyboardInterrupt, SystemExit):
        logging.info('received exit signal')
        disable_le_scan(sock)
        logging.info('waiting for background operations to complete')
        exit_event.set()


class TestClassAtcReader:
    def test_pvvx_parser(self):
        data = bytearray.fromhex('1312161a18332211ccbbaa670981108f0b54af04')
        r = parse_pvvx_format(data)
        assert r != None
        assert r.temperature == 24.07
        assert r.humidity == 42.25
        assert r.voltage == 2.959

    def test_atc_parser(self):
        data = bytearray.fromhex('1110161a18332211ccbbaa00f02a540b8faf')
        r = parse_atc_format(data)
        assert r != None
        assert r.temperature == 24.0
        assert r.humidity == 42
        assert r.voltage == 2.959
