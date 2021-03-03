FROM python:3

RUN apt-get install libbluetooth-dev

RUN pip install bluepy
RUN pip install pybluez
RUN pip install requests
RUN pip install dynaconf
RUN pip install requests
RUN git clone https://github.com/colin-guyon/py-bluetooth-utils.git
RUN cp py-bluetooth-utils/bluetooth_utils.py .
#confuse expects yaml extn
COPY config_default.yml config_default.yaml 
COPY mi_atc_reader.py mi_atc_reader.py

ENTRYPOINT python ./mi_atc_reader.py
#ENTRYPOINT /bin/bash