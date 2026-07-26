#!/usr/bin/env python3
"""Aggregate recent prod access logs from Better Stack and ask a local coding
agent (codex/claude) to recommend Cloudflare WAF rules. Print-only — does not
touch the CF API.

Usage:
    set -a; . .env; set +a
    source .venv/bin/activate
    tools/scan_bot_traffic.py [--days 2] [--agent codex|claude] [--dry-run] [--out -|path]

Reads BETTER_STACK_TOKEN from env (the Basic-auth `user:pass` string from
betterstack.txt). The host and source-table name are hard-coded.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request

BS_HOST = "eu-fsn-3-connect.betterstackdata.com:443"
BS_URL = f"https://{BS_HOST}?output_format_pretty_row_numbers=0"
# Hot tier (last ~25 min) and s3 archive (older). UNION ALL — the boundary
# is approximate, so very thin overlap is possible but harmless for top-N
# aggregates.
BS_HOT = "remote(t531521_explainshell_do_logs)"
BS_S3 = "s3Cluster(primary, t531521_explainshell_do_s3)"

log = logging.getLogger("scan_bot_traffic")


def _query(token: str, sql: str) -> list[dict]:
    """POST one SQL statement to Better Stack ClickHouse, return JSONEachRow rows."""
    body = sql.encode("utf-8")
    req = urllib.request.Request(BS_URL, data=body, method="POST")
    req.add_header("Content-Type", "text/plain")
    req.add_header(
        "Authorization", "Basic " + base64.b64encode(token.encode()).decode()
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(
            f"Better Stack query failed: HTTP {e.code}\nSQL: {sql[:200]}...\n{detail}"
        ) from e
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            log.warning("non-JSON row dropped: %s", line[:200])
    return rows


# --- SQL fragments ----------------------------------------------------------
# Common projections from the raw Caddy JSON column. Header values are arrays
# in Caddy's filter format (see _h() in tools/tail_explain_cmds.py); the
# trailing `1` index pulls the first value.


# Caddy access JSON is nested inside the Better Stack envelope under
# message_json (see `tools/tail_explain_cmds.py` for the un-wrapped shape).
# All extracts here go through that prefix.
def _source(n: int, unit: str) -> str:
    """Union of hot tier + s3 archive, restricted to the lookback window.

    Hot tier retains ~25 min; s3 covers everything older. UNION ALL plus the
    same window predicate on each side keeps the boundary handling simple —
    a row briefly visible in both sources would be double-counted, but the
    overlap is negligible vs. the cost of missing the s3 portion entirely.
    """
    where = (
        f"dt >= now() - INTERVAL {n} {unit} AND JSONHas(raw, 'message_json', 'request')"
    )
    return (
        "(\n"
        f"  SELECT raw, dt FROM {BS_HOT} WHERE {where}\n"
        "  UNION ALL\n"
        f"  SELECT raw, dt FROM {BS_S3} WHERE _row_type = 1 AND {where}\n"
        ")"
    )


def _qs(source: str) -> dict[str, str]:
    return {
        # 1. Top user agents.
        "top_user_agents": f"""
            SELECT
                JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1) AS ua,
                count() AS count,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'client_ip')) AS distinct_ips,
                round(countIf(JSONExtractInt(raw, 'message_json', 'status') BETWEEN 400 AND 499) / count(), 3) AS p_4xx,
                round(countIf(JSONExtractInt(raw, 'message_json', 'status') BETWEEN 500 AND 599) / count(), 3) AS p_5xx
            FROM {source}
            GROUP BY ua
            ORDER BY count DESC
            LIMIT 50
            FORMAT JSONEachRow
        """,
        # 2. Top client IPs.
        "top_client_ips": f"""
            SELECT
                JSONExtractString(raw, 'message_json', 'request', 'client_ip') AS ip,
                any(JSONExtractString(raw, 'message_json', 'request', 'headers', 'Cf-Ipcountry', 1)) AS country,
                count() AS count,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1)) AS distinct_uas,
                round(countIf(JSONExtractInt(raw, 'message_json', 'status') BETWEEN 400 AND 499) / count(), 3) AS p_4xx,
                topK(1)(JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1))[1] AS top_ua_for_ip
            FROM {source}
            GROUP BY ip
            ORDER BY count DESC
            LIMIT 50
            FORMAT JSONEachRow
        """,
        # 3. Top /24 buckets.
        "top_slash24": f"""
            SELECT
                IPv4NumToString(toUInt32(intDiv(IPv4StringToNum(JSONExtractString(raw, 'message_json', 'request', 'client_ip')), 256) * 256)) AS slash24,
                count() AS count,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'client_ip')) AS distinct_ips,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1)) AS distinct_uas
            FROM {source}
            WHERE match(JSONExtractString(raw, 'message_json', 'request', 'client_ip'), '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+\\\\.[0-9]+$')
            GROUP BY slash24
            ORDER BY count DESC
            LIMIT 50
            FORMAT JSONEachRow
        """,
        # 4. Top /explain cmd shapes (first token + arg-count signature).
        "top_cmd_shapes": f"""
            SELECT
                shape,
                count() AS count,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1)) AS distinct_uas,
                uniqExact(JSONExtractString(raw, 'message_json', 'request', 'client_ip')) AS distinct_ips
            FROM (
                SELECT
                    raw,
                    decodeURLFormComponent(extractURLParameter(JSONExtractString(raw, 'message_json', 'request', 'uri'), 'cmd')) AS cmd_decoded,
                    concat(
                        splitByChar(' ', cmd_decoded)[1],
                        ':args=',
                        toString(length(splitByChar(' ', cmd_decoded)) - 1),
                        ':pipes=',
                        toString(length(splitByChar('|', cmd_decoded)) - 1)
                    ) AS shape
                FROM {source}
                WHERE startsWith(JSONExtractString(raw, 'message_json', 'request', 'uri'), '/explain')
                  AND extractURLParameter(JSONExtractString(raw, 'message_json', 'request', 'uri'), 'cmd') != ''
            )
            GROUP BY shape
            ORDER BY count DESC
            LIMIT 100
            FORMAT JSONEachRow
        """,
        # 5. Top paths overall with status mix.
        "top_paths": f"""
            SELECT
                path,
                count() AS count,
                countIf(status BETWEEN 200 AND 299) AS s_2xx,
                countIf(status BETWEEN 300 AND 399) AS s_3xx,
                countIf(status BETWEEN 400 AND 499) AS s_4xx,
                countIf(status BETWEEN 500 AND 599) AS s_5xx
            FROM (
                SELECT
                    splitByChar('?', JSONExtractString(raw, 'message_json', 'request', 'uri'))[1] AS path,
                    JSONExtractInt(raw, 'message_json', 'status') AS status
                FROM {source}
            )
            GROUP BY path
            ORDER BY count DESC
            LIMIT 50
            FORMAT JSONEachRow
        """,
        # 6. Per-minute single-IP burst windows.
        "top_bursts": f"""
            SELECT
                toString(toStartOfMinute(dt)) AS minute,
                JSONExtractString(raw, 'message_json', 'request', 'client_ip') AS ip,
                any(JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1)) AS sample_ua,
                count() AS count
            FROM {source}
            GROUP BY minute, ip
            ORDER BY count DESC
            LIMIT 50
            FORMAT JSONEachRow
        """,
        # Raw samples: most-recent /explain?cmd= 200s, decoded.
        "samples_explain_200": f"""
            SELECT
                toString(dt) AS ts,
                JSONExtractString(raw, 'message_json', 'request', 'client_ip') AS ip,
                JSONExtractString(raw, 'message_json', 'request', 'headers', 'Cf-Ipcountry', 1) AS country,
                JSONExtractString(raw, 'message_json', 'request', 'headers', 'User-Agent', 1) AS ua,
                decodeURLFormComponent(extractURLParameter(JSONExtractString(raw, 'message_json', 'request', 'uri'), 'cmd')) AS cmd
            FROM {source}
            WHERE startsWith(JSONExtractString(raw, 'message_json', 'request', 'uri'), '/explain')
              AND JSONExtractInt(raw, 'message_json', 'status') = 200
              AND extractURLParameter(JSONExtractString(raw, 'message_json', 'request', 'uri'), 'cmd') != ''
            ORDER BY dt DESC
            LIMIT 20
            FORMAT JSONEachRow
        """,
    }


def collect(token: str, n: int, unit: str) -> dict[str, list[dict]]:
    source = _source(n, unit)
    out: dict[str, list[dict]] = {}
    for name, sql in _qs(source).items():
        log.info("querying %s ...", name)
        out[name] = _query(token, sql)
        log.info("  %s -> %d rows", name, len(out[name]))
    return out


# --- Agent prompt -----------------------------------------------------------

_SYSTEM = """You are an SRE reviewing access-log aggregates from explainshell
(a small public Flask service behind Cloudflare). Recommend Cloudflare WAF
rules that target abusive bot traffic visible in the data below. Be
conservative: only suggest a rule when the evidence is strong and false
positives are unlikely. The site has real human traffic; legit users hit
/explain with arbitrary shell commands, sometimes via curl.

The data is six pre-aggregated dimensions plus a few raw samples. Window:
{window}.

Output a single JSON object (you may surround it with prose, but the JSON
itself must parse) with this exact schema:

{{
  "recommendations": [
    {{
      "rule_type": "block|challenge|rate_limit",
      "match": "<CF expression, e.g. (http.user_agent contains \\"FooBot\\")>",
      "evidence": "<which dimension(s) and counts>",
      "false_positive_risk": "low|medium|high",
      "notes": "<one-line caveat>"
    }}
  ],
  "no_action_reasons": ["<dimensions that looked suspicious but aren't worth a rule, with why>"]
}}
"""


def build_prompt(window: str, summary: dict[str, list[dict]]) -> str:
    parts = [_SYSTEM.format(window=window), ""]
    for name, rows in summary.items():
        parts.append(f"### {name} ({len(rows)} rows)")
        parts.append("```json")
        parts.append(json.dumps(rows, indent=2, ensure_ascii=False))
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


# --- Agent runner -----------------------------------------------------------


def _which(binary: str) -> bool:
    return (
        subprocess.run(
            ["which", binary], capture_output=True, text=True, check=False
        ).returncode
        == 0
    )


def run_agent(agent: str, prompt: str) -> str:
    if agent == "codex":
        if not _which("codex"):
            raise SystemExit("codex CLI not found in PATH")
        cmd = ["codex", "exec", "--skip-git-repo-check", "-"]
        log.info("invoking %s ...", " ".join(cmd))
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, check=False
        )
    elif agent == "claude":
        if not _which("claude"):
            raise SystemExit("claude CLI not found in PATH")
        cmd = ["claude", "-p", prompt]
        log.info("invoking claude -p (prompt elided) ...")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    else:
        raise SystemExit(f"unknown agent: {agent}")

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"{agent} exited {proc.returncode}")
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr)
    return proc.stdout


# --- CLI --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    win = ap.add_mutually_exclusive_group()
    win.add_argument("--days", type=int, help="lookback window in days (default: 2)")
    win.add_argument("--hours", type=int, help="lookback window in hours")
    ap.add_argument(
        "--agent",
        choices=("codex", "claude"),
        default="codex",
        help="local coding agent to invoke",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print aggregated summary as JSON and exit (no agent call)",
    )
    ap.add_argument(
        "--out",
        default="-",
        help="write agent response here (default: stdout)",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="log query progress to stderr"
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )

    token = os.environ.get("BETTER_STACK_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BETTER_STACK_TOKEN not set. Run `set -a; . .env; set +a` first."
        )
    if ":" not in token:
        log.warning(
            "BETTER_STACK_TOKEN does not look like 'user:pass'; "
            "Basic auth may fail. See betterstack.txt."
        )

    if args.hours is not None:
        n, unit, window = args.hours, "HOUR", f"{args.hours} hour(s)"
    else:
        days = args.days if args.days is not None else 2
        n, unit, window = days, "DAY", f"{days} day(s)"

    summary = collect(token, n, unit)

    if args.dry_run:
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    prompt = build_prompt(window, summary)
    response = run_agent(args.agent, prompt)

    if args.out == "-":
        sys.stdout.write(response)
        if not response.endswith("\n"):
            sys.stdout.write("\n")
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(response)
        log.info("wrote response to %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
