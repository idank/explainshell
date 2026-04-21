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
# DB_NAME pins the release asset this image baked in. The deploy
# pipeline (CI workflow and `make deploy-local`) looks it up from the
# db-latest release and passes it here. It serves two roles:
#   1) cache bust — referenced in the RUN string, so a new DB in the
#      release invalidates this layer and forces re-download.
#   2) content pin — the script verifies sha256 against the release
#      metadata, so what lands in the image is exactly what CI captured
#      (not "whatever is newest at build time", which would race with
#      concurrent uploads).
ARG DB_NAME
RUN tools/download-latest-db.sh --asset "$DB_NAME" explainshell.db

COPY prod/docker/Caddyfile /etc/caddy/Caddyfile
COPY prod/docker/start.sh .
COPY explainshell/ explainshell/

# Surfaced in cache ETags so a code-only deploy still invalidates.
# Passed in by the deploy pipeline (see the deploy workflows under
# .github/workflows/).
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

EXPOSE 8080

CMD ["./start.sh"]
