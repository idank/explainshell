# syntax=docker/dockerfile:1
FROM python:2.7.18-slim
LABEL maintainer="Simon Toivo Telhaug <simon.toivo@gmail.com>"

# Enable man to work inside container
RUN sed -e 's@\(.*/usr/share/man\)@#\1@'   \
        -e 's@\(.*/usr/share/groff\)@#\1@' \
        -i /etc/dpkg/dpkg.cfg.d/docker

# Update and install necessary packages
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y findutils groff-base make \
                       man-db mongo-tools \
 && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

# Add requirements for application
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /tmp/requirements.txt

# Create unprivileged user
RUN addgroup --system --gid 500 es \
 && adduser  --system --gid 500 --uid 500 --home /opt/webapp es \
 && chown -R es:es /opt/webapp /usr/local/lib/python2.7/site-packages

USER es
COPY --chown=es:es ./ /opt/webapp/

WORKDIR /opt/webapp
EXPOSE 5000

CMD ["/bin/bash", "start.sh"]
