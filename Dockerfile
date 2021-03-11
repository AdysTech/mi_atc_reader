FROM python:3-slim as base_setup

#copy the default config file
COPY config_default.yml config_default.yaml 
COPY mi_atc_reader.py mi_atc_reader.py
COPY requirements.txt requirements.txt
COPY entry.sh entry.sh

RUN apt-get update \
    && apt-get install -y --no-install-recommends  \
        gcc \
        git \	
        libbluetooth3 \
        libbluetooth-dev \
        python-dev \
    && pip install -r requirements.txt \
    && git clone https://github.com/colin-guyon/py-bluetooth-utils.git --depth=1 \
    && cp py-bluetooth-utils/bluetooth_utils.py . \
    && apt-get remove -y \
        gcc \
        git \
        libbluetooth-dev \
        python-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*


from base_setup as test_image
RUN pip install pytest
RUN python -m pytest mi_atc_reader.py -rA

from base_setup as final_image
LABEL "contact"="info@adystech.com"
LABEL repo="https://github.com/AdysTech/mi_atc_reader/"
LABEL version="1.0"
LABEL description="Provides a python script as a docker service  \
which can read BLE advertisements from Xiaomi Smart Bluetooth Thermometer & Hygrometer. \
Thermometers needs to be running custom open source firmware from pvvx or atc1441"
CMD [ "./entry.sh" ]