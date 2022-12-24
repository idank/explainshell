FROM python:2.7

RUN apt-get update \
  && apt-get install man-db -y \
  && apt-get clean

ADD ./requirements.txt /tmp/requirements.txt

RUN pip install --upgrade pip \
  && python --version \
  && pip install -r /tmp/requirements.txt \
  && rm -rf ~/.cache/pip/*

ADD ./ /opt/webapp/
WORKDIR /opt/webapp
EXPOSE 5000

CMD ["make", "serve"]
