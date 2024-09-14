FROM python:3.12

RUN apt update \
  && apt install man-db -y \
  && apt clean

WORKDIR /opt/webapp
COPY . .

RUN pip3 install --no-cache-dir --no-warn-script-location --upgrade pip setuptools wheel virtualenv \
  && pip3 install --no-cache-dir --no-warn-script-location -r requirements.txt

EXPOSE 5000

CMD ["python3", "runserver.py"]
