FROM python:2.7

RUN echo 'deb http://archive.debian.org/debian/ stretch contrib main non-free' > /etc/apt/sources.list

RUN apt-get update \
  && apt-get install -y --allow-remove-essential \
    man-db \
    bsdmainutils \
    libncurses5 \
    libtinfo5 \
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
