#!/usr/bin/env python3
"""module to listen for BLE aderts from Xiaomi Mijia thermometers running custom firstmare and post to Influx or mqtt"""
import sys
import os
import time
import logging
import threading
import signal
import json
from dataclasses import dataclass, asdict
from collections import deque
import struct
import requests
from dynaconf import Dynaconf
import bluetooth._bluetooth as bluez
import paho.mqtt.client as mqtt
from bluetooth_utils import (toggle_device, enable_le_scan,
                             parse_le_advertising_events,
                             disable_le_scan)

#################################


@dataclass
class SensorReading:
    """ Sensor reading class, covers both flavors of firmware. """
    temperature: float = 0
    humidity: int = 0
    voltage: float = 0
    battery: int = 0
    timestamp: int = 0
    sensor: dict = None
    counter: int = 0

    def from_dict(self, data):
        """constructor"""
        if not isinstance(data, dict):
            raise ValueError(f"{type(data)} is Not a dict")
        try:
            self.temperature = data['temperature']
            self.humidity = data['humidity']
            self.voltage = data['voltage']
            self.battery = data['battery']
            self.timestamp = data['timestamp']
            self.sensor = data['sensor']
        except (ModuleNotFoundError, ValueError, AttributeError, TypeError) as error:
            raise TypeError(
                f"got '{type(data)!r}' object, couldn't convert to SensorReading") from error


def le_advertise_packet_handler(mac, adv_type, data, rssi):
    """BLE advertisement handler which will unpack and extract sensor reading.

    This function checks if its from Mijia atc firmware to make sure we are
    getting this message from one of ATC firmware forks.
    Both atc1441 and pvvx forks send data on GATT Service 0x181A Environmental Sensing
    """
    exit_stat = not exit_event.is_set()
    if struct.unpack('<H', data[3:5])[0] != 0x181A:
        return exit_stat

    # data format adds mac as part of data structure.
    # just another check to ensure we are not listening to any other BLE on 0x181A
    data_mac, pvvx = get_data_mac(data)
    if mac.upper() != data_mac.hex(':').upper():
        return exit_stat

    reading = parse_pvvx_format(data) if pvvx else parse_atc_format(data)
    if reading is None:
        return exit_stat

    if config.discovery_mode:
        logging.info(
            f"Device: {mac} firmware: {'pvvx' if pvvx else 'atc1441'} Temp: {reading.temperature}c Humidity: {reading.humidity}% Batt: {reading.battery}% ({reading.voltage}v)")

    if len(config.thermometers) > 0:
        # this is not needed, but pylint won't let me use variable from walrus below.
        match = ""
        if any((match := t)['mac'] == mac for t in config.thermometers):
            reading.sensor = match
            readingQueue.append(reading)
    return exit_stat


def handle_retry(reading: SensorReading):
    """Currently only handles errors in posting to influxdb. Adds the message back to queue"""
    if len(readingQueue) < config.errorbuffer.max_items:
        readingQueue.appendleft(reading)
    else:
        logging.warning(f'retry queue full, Discoreded Message: {reading}!')
    exit_event.wait(5)


def post_influx(reading: SensorReading):
    """Post the message to Influxdb using their line protocol"""
    tags = ','.join([f"{key}={value}" for key,
                     value in reading.sensor['tags'].items()])
    payload = f"{config.influxdb.measurement},{tags},name={reading.sensor['name']},mac={reading.sensor['mac']} temperature={reading.temperature},humidity={reading.humidity},battery={reading.battery},voltage={reading.voltage} {reading.timestamp}"
    try:
        response = requests.post(influxdb_write_endpoint,
                                 data=payload, timeout=1)
        # ', return:{r.status_code}')
        logging.debug(
            f'url:{influxdb_write_endpoint}, data: {payload}')
        if response.status_code != 204:
            if response.status_code == 404:
                logging.warning(
                    "Influxdb database missing!!.. manually create the DB.")
                handle_retry(reading)
            else:
                logging.warning(
                    f'Failed to save{reading} due to {response.text}')
    except requests.Timeout:
        logging.warning("Influxdb Timeout.. retrying")
        handle_retry(reading)
    except requests.ConnectionError:
        logging.warning(
            "Unable to connect to InfluxDB.. retrying")
        handle_retry(reading)


def publish_mqtt(reading: SensorReading):
    """Publish sesnor reading to MQTT broker using paho client"""
    mqtt_client.publish(
        f"{config.mqtt.topic_prefix}/{reading.sensor['name']}/reading", json.dumps(asdict(reading), default=str))


def deque_thread():
    """background processing which will dequeue the reading and process it"""
    while True:
        try:
            logging.debug(f"Reading Queue Depth: {len(readingQueue)}")
            reading = readingQueue.popleft()
            logging.debug(reading)
            if config.influxdb.enabled:
                post_influx(reading)
            if config.mqtt.enabled:
                publish_mqtt(reading)
        except IndexError:
            exit_event.wait(1)
        except Exception as err:
            logging.exception(err)
        if exit_event.is_set():
            if len(readingQueue) > 0:
                logging.warning(f"Queue had {len(readingQueue)} unsaved items")
            break


def get_data_mac(data):
    """Extract mac address of the sensor. This also is the loic where we identify the firmware flavour

    # ref: https://github.com/pvvx/ATC_MiThermometer#bluetooth-advertising-formats
    # this code will support both ATC format and pvvx's custom format.
    # https://github.com/pvvx/ATC_MiThermometer/blob/c3b89a7eddad21c054e352b64af21654d5112421/src/ble.h#L90
     """
    pvvx = False
    data_mac = bytearray(data[5:11])
    # pvvx format sends everything in little endian format. Python assumes big endian.
    if len(data) > 18 and len(data) < 22:
        data_mac.reverse()
        pvvx = True
    return data_mac, pvvx


def parse_atc_format(data) -> SensorReading:
    """Transform raw data in atc firmware flavour into sensor reading"""
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
    """Transform raw data in pvvx firmware flavour into sensor reading"""
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


def load_config() -> Dynaconf:
    """load default config and apply any overrides. Returns Dynacof config objet"""
    try:
        config = Dynaconf(
            settings_files=["config_default.yaml", "custom.yml"],
            envvar_prefix="ATC",
        )
    except:
        sys.stderr.write("Couldn't load config files")
        raise
    return config


def exit_gracefully(signum, stack_frame):
    """handles exit signals, closes the background thread"""
    # Raises SystemExit(0):
    logging.log(level=log_level, msg=f'received signal{signum}')
    if config.mqtt.enabled:
        mqtt_client.publish(f"{config.mqtt.topic_prefix}/status",
                            payload="Offline", qos=0, retain=True)
        mqtt_client.disconnect()
        mqtt_client.loop_stop()
    exit_event.set()
    sys.exit(0)


def connect_mqtt() -> mqtt.Client:
    """Connect to MQTT broker using paho library"""
    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            logging.info(f'Connected to mqtt broker at {config.mqtt.broker}')
            client.publish(f"{config.mqtt.topic_prefix}/status",
                           payload="Online", qos=0, retain=True)
        else:
            logging.error(f'Failed to connect, return code {rc}')
    client = mqtt.Client(config.mqtt.client_id)
    client.username_pw_set(config.mqtt.username, config.mqtt.password)
    client.on_connect = on_connect
    client.will_set(f"{config.mqtt.topic_prefix}/status",
                    payload="Offline", qos=0, retain=True)
    client.connect(config.mqtt.broker, config.mqtt.port, 60)
    client.loop_start()
    return client


if __name__ == '__main__':  # has a blocking call at the end
    signal.signal(signal.SIGINT, exit_gracefully)
    signal.signal(signal.SIGTERM, exit_gracefully)
    config = load_config()
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

    if config.mqtt.enabled:
        mqtt_client = connect_mqtt()

    # Set filter to "True" to see only one packet per device
    logging.info('Start scanning for BLE messages')
    enable_le_scan(sock, filter_duplicates=False)

    exit_event = threading.Event()
    readingQueue = deque()
    queueThread = threading.Thread(target=deque_thread)
    # queueThread.daemon = True
    queueThread.start()

    try:
        # Blocking call (the given handler will be called each time a new LE
        # advertisement packet is detected)
        parse_le_advertising_events(sock,
                                    handler=le_advertise_packet_handler,
                                    debug=config.ble.debug)
    # Scan until Ctrl-C
    except (KeyboardInterrupt, SystemExit, StopIteration):
        logging.info('received exit signal')
        disable_le_scan(sock)
        logging.info('waiting for background operations to complete')
        exit_event.set()


class TestClassAtcReader:
    """Test class to make sure parsing logic works"""

    def test_pvvx_parser(self):
        """Test pvvx parsing"""
        data = bytearray.fromhex('1312161a18332211ccbbaa670981108f0b54af04')
        r = parse_pvvx_format(data)
        assert r is not None
        assert r.temperature == 24.07
        assert r.humidity == 42.25
        assert r.voltage == 2.959

    def test_atc_parser(self):
        """Test atc parsing"""
        data = bytearray.fromhex('1110161a18332211ccbbaa00f02a540b8faf')
        r = parse_atc_format(data)
        assert r is not None
        assert r.temperature == 24.0
        assert r.humidity == 42
        assert r.voltage == 2.959
