FROM python:2.7

MAINTAINER Simon Toivo Telhaug <simon.toivo@gmail.com>

RUN apt-get update \
&& apt-get install man-db -y

ADD ./requirements.txt /tmp/requirements.txt

RUN pip install --upgrade pip \
&& python --version \
&& pip install -r /tmp/requirements.txt

ADD ./ /opt/webapp/
WORKDIR /opt/webapp
EXPOSE 5000

CMD ["make", "serve"]
