FROM python:3 as base_setup

RUN apt-get install libbluetooth-dev
RUN pip install bluepy
RUN pip install pybluez
RUN pip install requests
RUN pip install dynaconf
RUN git clone https://github.com/mvadu/py-bluetooth-utils.git --depth=1
RUN cp py-bluetooth-utils/bluetooth_utils.py .

#copy the default config file
COPY config_default.yml config_default.yaml 
COPY mi_atc_reader.py mi_atc_reader.py

from base_setup
RUN pip install pytest
RUN python -m pytest mi_atc_reader.py -rA

from base_setup
LABEL "contact"="info@adystech.com"
LABEL repo="https://github.com/AdysTech/mi_atc_reader/"
LABEL version="1.0"
LABEL description="Provides a python script as a docker service  \
which can read BLE advertisements from Xiaomi Smart Bluetooth Thermometer & Hygrometer. \
Thermometers needs to be running custom open source firmware from pvvx or atc1441"
ENTRYPOINT python ./mi_atc_reader.py
#ENTRYPOINT /bin/bash