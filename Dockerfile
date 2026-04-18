FROM caddy:2 AS caddy-src

FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends wget zstd jq \
  && rm -rf /var/lib/apt/lists/*

COPY --from=caddy-src /usr/bin/caddy /usr/local/bin/caddy

WORKDIR /opt/webapp
COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-warn-script-location -r requirements.txt

COPY tools/download-latest-db.sh tools/
RUN tools/download-latest-db.sh explainshell.db

COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh .
COPY explainshell/ explainshell/

EXPOSE 8080

CMD ["./start.sh"]
