# mi_atc_reader


Xiaomi makes Mijia (LYWSD03MMC) & Xiaomi Miaomiaoce (MHO-C401) Smart Bluetooth Thermometer & Hygrometer devices, which are tiny and have very accurate sensors on them. THe default firmware is designed to work with Xiaomi app, but they are hacker friendly. 

Two open source contributors [atc1441](https://github.com/pvvx/ATC_MiThermometer) and [pvvx](https://github.com/pvvx/ATC_MiThermometer) provided an open source alternate firmware which allows the device to transmit their readings over BLE advertisement.

This repo provides a python module (with options to run via docker:[adystech/mi_atc_reader](https://hub.docker.com/r/adystech/mi_atc_reader)) to consume  those BLE advertisements and write to other data stores (e.g. influxdb)

The configuration ([custom.yml](./custom.yml)) provides placeholders to tag the individual devices with a friendly name, and other tags. Any number of key/value pairs can be provided in the tags and they all will be posted to influx.

It also supports device discovery so that before the devices can be added to config, they can be recognized. Its suggested to use start the python program, and look at the logs for the discovered devices.

## Installation
### Standalone
    - pip install -r requirements.txt
    - python mi_atc_reader.py

### docker

    #### standalone service
    `docker service create --name mi_atc_reader --cap-add NET_ADMIN --mount type=bind,source=custom.yml,destination=/custom.yml adystech/mi_atc_reader`

    #### swarm docker stack deploy
    use docker-compose like below

    ```yml
    version: '3.8'

    services:
    atc_reader:
        image: adystech/mi_atc_reader:latest
        volumes:
        - config/custom.yml:/custom.yml
        cap_add:
        - NET_ADMIN
        networks:
        - host_net
    networks:
    host_net:
        external: true
        name: host
    ```

    `docker stack deploy -c docker-compose.yml mi_reader`