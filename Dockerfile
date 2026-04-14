FROM python:3.12-slim

ARG DB_URL=https://github.com/idank/explainshell/releases/download/db-latest/explainshell.db.zst

RUN apt-get update \
  && apt-get install -y --no-install-recommends wget zstd \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/webapp
COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-warn-script-location -r requirements.txt

RUN wget -q -O explainshell.db.zst "$DB_URL" \
  && sha256sum explainshell.db.zst | awk '{print $1}' > explainshell.db.sha256 \
  && zstd -d --rm explainshell.db.zst

COPY start.sh .
COPY explainshell/ explainshell/

EXPOSE 8080

CMD ["./start.sh"]
