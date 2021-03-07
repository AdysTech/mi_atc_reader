# mi_atc_reader


Xiaomi makes Mijia (LYWSD03MMC) & Xiaomi Miaomiaoce (MHO-C401) Smart Bluetooth Thermometer & Hygrometer devices, which are tiny and have very accurate sensors on them. THe default firmware is designed to work with Xiaomi app, but they are hacker friendly. 

Two open source contributors [atc1441](https://github.com/pvvx/ATC_MiThermometer) and [pvvx](https://github.com/pvvx/ATC_MiThermometer) provided an open source alternate firmware which allows the device to transmit their readings over BLE advertisement.

This repo provides a python module (with options to run via docker:[adystech/mi_atc_reader](https://hub.docker.com/r/adystech/mi_atc_reader)) to consume  those BLE advertisements and write to other data stores (e.g. influxdb)

The configuration ([custom.yml](./custom.yml)) provides placeholders to tag the individual devices with a friendly name, and other tags. Any number of key/value pairs can be provided in the tags and they all will be posted to influx.

It also supports device discovery so that before the devices can be added to config, they can be recognized. Its suggested to use start the python program, and look at the logs for the discovered devices.

## Installation
### Standalone
    - Refer the docker file for dependencies, and install those packages.
    - run mi_atc_reader.py

### docker
    pull the image `docker pull adystech/mi_atc_reader`
    start mi_atc_reader image with access to host network `--net=host --privileged` which is required for the image to see the bluetooth devices.
    custom.yml needs to be mapped to  `/custom.yml` for the image to recognize it.