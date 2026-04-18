"""Load-test explainshell locally, reproducing the bot traffic patterns seen in prod.

Two modes:
  http     — fire HTTP requests at a running server (closed- or open-loop)
  profile  — run the /explain code path in-process under cProfile

Examples:
  # Closed-loop (fixed concurrency): measures max sustained throughput.
  python tools/loadtest.py http --url http://127.0.0.1:5000 -c 8 -d 30

  # Open-loop (fixed arrival rate): measures degradation under target load.
  python tools/loadtest.py http --url http://127.0.0.1:5000 --rps 4 -d 60

  # Mixed workload (30% simple human-like commands, 70% bot permutations).
  python tools/loadtest.py http --url http://127.0.0.1:5000 --rps 4 -d 60 --simple-ratio 0.3

  # Per-bucket latency + JSON output for cross-server comparisons.
  python tools/loadtest.py http --url http://127.0.0.1:5000 --rps 4 -d 60 \\
      --bucket-seconds 10 --json > results.json

  # In-process profile (no HTTP, no gunicorn).
  python tools/loadtest.py profile --n 100 --db explainshell.db
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json as _json
import pstats
import random
import statistics
import sys
import time
from dataclasses import dataclass

# --- variant pools ----------------------------------------------------------
# Token-level variants the bot rotates through when permuting bash pipelines.
# Based on commands observed in production (meta-webindexer and fake-Chrome
# swarms), which iterate through man-section suffixes and plan9/rust variants
# of common utilities at each slot.

OPENER_VARIANTS = ["cd", "cd.1", "cd.1posix", "shift", "rc9", "exec.1posix"]
CAT_VARIANTS = ["cat", "cat.1", "cat.1posix", "plan9-cat.1", "gnucat.1", "rust-cat.1"]
READ_VARIANTS = ["plan9-read.1", "read.1posix"]
SORT_VARIANTS = ["sort", "sort.1", "sort.1posix", "plan9-sort.1", "gnusort.1"]
AWK_VARIANTS = ["awk", "awk.1posix", "plan9-awk.1"]
TR_VARIANTS = ["tr", "tr.1", "tr.1posix", "plan9-tr.1", "gnutr.1"]
SED_VARIANTS = ["sed", "sed.1", "sed.1posix", "plan9-sed.1"]
CUT_VARIANTS = ["cut", "cut.1", "cut.1posix"]
GREP_VARIANTS = ["grep", "grep.1", "grep.1posix", "plan9-grep.1"]
UNIQ_VARIANTS = ["uniq", "uniq.1", "uniq.1posix"]
XARGS_VARIANTS = ["xargs", "xargs.1", "xargs.1posix"]
TOUCH_VARIANTS = ["touch", "touch.1", "touch.1posix", "plan9-touch.1"]
EVAL_VARIANTS = ["eval", "eval.1posix", "urxvt-eval"]
PERL_VARIANTS = ["perl", "perl.1", "perlrun.1", "perl5.14.2.1"]
LN_VARIANTS = ["ln", "ln.1", "ln.1posix"]
LS_VARIANTS = ["ls", "ls.1", "ls.1posix", "plan9-ls.1"]


def _pick(pool: list[str], rng: random.Random) -> str:
    return rng.choice(pool)


# --- command templates ------------------------------------------------------
# Each generator returns a single bash pipeline matching one of the attack
# shapes observed in prod. gen_bot_cmd picks one uniformly at random.


def gen_amass_cmd(rng: random.Random) -> str:
    """amass-enum pipeline (meta-webindexer shape)."""
    c1, c2, c3 = (_pick(CAT_VARIANTS + READ_VARIANTS, rng) for _ in range(3))
    s1, s2, s3, s4 = (_pick(SORT_VARIANTS, rng) for _ in range(4))
    a1, a2 = _pick(AWK_VARIANTS, rng), _pick(AWK_VARIANTS, rng)
    t1 = _pick(TR_VARIANTS, rng)
    se1, se2, se3 = (_pick(SED_VARIANTS, rng) for _ in range(3))
    cu1, cu2 = _pick(CUT_VARIANTS, rng), _pick(CUT_VARIANTS, rng)
    return (
        f"amass enum -src -ip -active -brute -d navy.mil -o domain ; "
        f"{c1} domain | {cu1} -d']' -f 2 | {a1} '{{print $1}}' | {s1} -u > hosts-amass.txt ; "
        f"{c2} domain | {cu2} -d']' -f2 | {a2} '{{print $2}}' | {t1} ',' '\\n' | {s2} -u > ips-amass.txt ; "
        f"curl -s \"https://crt.sh/?q=%.navy.mil&output=json\" | jq '.[].name_value' | "
        f"{se1} 's/\\\"//g' | {se2} 's/\\*\\.//g' | {s3} -u > hosts-crtsh.txt ; "
        f"{se3} 's/$/.navy.mil/' dns-Jhaddix.txt_cleaned > hosts-wordlist.txt ; "
        f"{c3} hosts-amass.txt hosts-crtsh.txt hosts-wordlist.txt | {s4} -u > hosts-all.txt"
    )


def gen_git_clone_cmd(rng: random.Random) -> str:
    """git-clone-in-temp pipeline (fake-Chrome swarm shape)."""
    op1, op2, op3 = (_pick(OPENER_VARIANTS, rng) for _ in range(3))
    ev = _pick(EVAL_VARIANTS, rng)
    to = _pick(TOUCH_VARIANTS, rng)
    return (
        f"{op1} $(mktemp -d); git init a; "
        f"({ev} a; {to} a; git add a; git commit -m a); "
        f"git clone a b; ({op2} b; git checkout -b ignore-this); "
        f"git clone a b2; ({op3} b2; git fetch origin -- --force --oneline -b -d -m)"
    )


def gen_log_processing_cmd(rng: random.Random) -> str:
    """geoip / failedcomments pipeline (observed in misc bot traffic)."""
    c1 = _pick(CAT_VARIANTS + READ_VARIANTS, rng)
    aw = _pick(AWK_VARIANTS, rng)
    xa = _pick(XARGS_VARIANTS, rng)
    se = _pick(SED_VARIANTS, rng)
    s1, s2 = _pick(SORT_VARIANTS, rng), _pick(SORT_VARIANTS, rng)
    un = _pick(UNIQ_VARIANTS, rng)
    return (
        f"{c1} failedcomments.log | "
        f'{aw} \'BEGIN {{ FS="|" }} {{ gsub(" ip: ", "", $2); print $2 }}\' | '
        f"{xa} -n1 geoiplookup | "
        f"{se} -e 's/GeoIP Country Edition: //' | "
        f"{s1} | {un} -c | {s2} -rn"
    )


def gen_perl_cleanup_cmd(rng: random.Random) -> str:
    """perl HTML tag-split pipeline."""
    rd = _pick(READ_VARIANTS, rng)
    p1, p2, p3 = (_pick(PERL_VARIANTS, rng) for _ in range(3))
    gr = _pick(GREP_VARIANTS, rng)
    return (
        f"{rd} - | {p1} -pe 's{{\\n}}{{ }}g' | "
        f"{p2} -pe 's{{>}}{{>\\n}}g' | "
        f"{p3} -pe 's{{<}}{{\\n<}}g' | {gr} -v '<'"
    )


def gen_hardlink_cmd(rng: random.Random) -> str:
    """Simple touch+ln+ls pipeline (shorter; amznbot-style shape)."""
    to = _pick(TOUCH_VARIANTS, rng)
    ln1, ln2 = _pick(LN_VARIANTS, rng), _pick(LN_VARIANTS, rng)
    ls = _pick(LS_VARIANTS, rng)
    return f"{to} datei; {ln1} datei hardlink; {ln2} -s datei softlink; {ls} -il *"


BOT_TEMPLATES = [
    gen_amass_cmd,
    gen_git_clone_cmd,
    gen_log_processing_cmd,
    gen_perl_cleanup_cmd,
    gen_hardlink_cmd,
]


def gen_bot_cmd(rng: random.Random) -> str:
    """Pick one of the bot templates uniformly at random and generate a cmd."""
    return rng.choice(BOT_TEMPLATES)(rng)


SIMPLE_CMDS = [
    "ls -la",
    "git commit -am 'msg'",
    "grep -rni foo .",
    "find . -name '*.py'",
    "tar xzvf archive.tar.gz",
    "curl -sL https://example.com",
    "ssh -p 22 user@host",
    "kubectl get pods -n default",
    "docker run --rm -it alpine sh",
    "awk '{print $1}' file.txt",
]


# --- results & stats --------------------------------------------------------


@dataclass
class Result:
    ok: bool
    status: int
    latency_ms: float
    bytes: int
    t_end_s: float = 0.0  # seconds since test start when the response returned
    err: str = ""


def pcts(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    s = sorted(values)
    n = len(s)

    def p(q: float) -> float:
        i = min(n - 1, int(q * n))
        return s[i]

    return {
        "n": n,
        "p50": p(0.50),
        "p90": p(0.90),
        "p95": p(0.95),
        "p99": p(0.99),
        "max": s[-1],
        "mean": statistics.mean(s),
    }


def _bucket_stats(
    results: list[Result], bucket_seconds: float, duration: float
) -> list[dict]:
    n_buckets = max(1, int(duration // bucket_seconds) + 1)
    buckets: list[list[Result]] = [[] for _ in range(n_buckets)]
    for r in results:
        b = int(r.t_end_s // bucket_seconds)
        if 0 <= b < n_buckets:
            buckets[b].append(r)
    rows = []
    for i, rs in enumerate(buckets):
        if not rs:
            rows.append({"bucket_s": i * bucket_seconds, "n": 0})
            continue
        oks = [r for r in rs if r.ok]
        lats = [r.latency_ms for r in oks]
        st = pcts(lats) if lats else {}
        rows.append(
            {
                "bucket_s": i * bucket_seconds,
                "n": len(rs),
                "ok": len(oks),
                "fail": len(rs) - len(oks),
                "rps": len(rs) / bucket_seconds,
                "p50": st.get("p50"),
                "p95": st.get("p95"),
                "p99": st.get("p99"),
                "max": st.get("max"),
            }
        )
    return rows


# --- HTTP load generation ---------------------------------------------------


async def _fire_one(
    session,
    url: str,
    rng: random.Random,
    results: list[Result],
    test_start: float,
    simple_ratio: float,
) -> None:
    if rng.random() < simple_ratio:
        cmd = rng.choice(SIMPLE_CMDS)
    else:
        cmd = gen_bot_cmd(rng)
    target = f"{url}/explain"
    t0 = time.perf_counter()
    try:
        async with session.get(target, params={"cmd": cmd}, timeout=60) as resp:
            body = await resp.read()
            lat = (time.perf_counter() - t0) * 1000.0
            results.append(
                Result(
                    ok=resp.status == 200,
                    status=resp.status,
                    latency_ms=lat,
                    bytes=len(body),
                    t_end_s=time.monotonic() - test_start,
                )
            )
    except Exception as e:
        lat = (time.perf_counter() - t0) * 1000.0
        results.append(
            Result(
                ok=False,
                status=0,
                latency_ms=lat,
                bytes=0,
                t_end_s=time.monotonic() - test_start,
                err=str(e)[:80],
            )
        )


async def _closed_loop_worker(
    session,
    url: str,
    rng: random.Random,
    results: list[Result],
    test_start: float,
    stop_at: float,
    simple_ratio: float,
) -> None:
    while time.monotonic() < stop_at:
        await _fire_one(session, url, rng, results, test_start, simple_ratio)


@dataclass
class LoopStats:
    dropped: int = 0
    scheduled: int = 0
    in_flight_peak: int = 0


async def _open_loop_scheduler(
    session,
    url: str,
    rng: random.Random,
    results: list[Result],
    test_start: float,
    stop_at: float,
    rps: float,
    simple_ratio: float,
    max_in_flight: int,
    stats: LoopStats,
) -> None:
    inter_arrival = 1.0 / rps
    next_fire = test_start
    in_flight: set[asyncio.Task] = set()

    def _release(t: asyncio.Task) -> None:
        in_flight.discard(t)

    while time.monotonic() < stop_at:
        now = time.monotonic()
        if now < next_fire:
            await asyncio.sleep(next_fire - now)
        if len(in_flight) >= max_in_flight:
            # Overloaded: client would normally back off; we drop the slot
            # and let the scheduler stay on cadence.
            stats.dropped += 1
        else:
            task = asyncio.create_task(
                _fire_one(session, url, rng, results, test_start, simple_ratio)
            )
            in_flight.add(task)
            task.add_done_callback(_release)
            stats.scheduled += 1
            stats.in_flight_peak = max(stats.in_flight_peak, len(in_flight))
        next_fire += inter_arrival

    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)


async def run_http(args: argparse.Namespace) -> dict:
    import aiohttp

    rng = random.Random(args.seed)
    results: list[Result] = []
    test_start = time.monotonic()
    stop_at = test_start + args.duration

    connector = aiohttp.TCPConnector(limit=max(args.concurrency * 2, 64))
    timeout = aiohttp.ClientTimeout(total=None)

    loop_stats = LoopStats()
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        if args.rps:
            await _open_loop_scheduler(
                session,
                args.url,
                rng,
                results,
                test_start,
                stop_at,
                args.rps,
                args.simple_ratio,
                args.concurrency,
                loop_stats,
            )
        else:
            workers = [
                asyncio.create_task(
                    _closed_loop_worker(
                        session,
                        args.url,
                        rng,
                        results,
                        test_start,
                        stop_at,
                        args.simple_ratio,
                    )
                )
                for _ in range(args.concurrency)
            ]
            await asyncio.gather(*workers)

    return _build_report(results, loop_stats, args)


def _build_report(
    results: list[Result],
    loop_stats: LoopStats,
    args: argparse.Namespace,
) -> dict:
    ok = [r for r in results if r.ok]
    fail = [r for r in results if not r.ok]
    lat = [r.latency_ms for r in ok]
    total = len(results)
    elapsed = float(args.duration)

    status_hist: dict[int, int] = {}
    err_hist: dict[str, int] = {}
    for r in fail:
        status_hist[r.status] = status_hist.get(r.status, 0) + 1
        if r.err:
            err_hist[r.err] = err_hist.get(r.err, 0) + 1

    report = {
        "mode": "open-loop" if args.rps else "closed-loop",
        "url": args.url,
        "duration_s": args.duration,
        "concurrency": args.concurrency,
        "target_rps": args.rps,
        "simple_ratio": args.simple_ratio,
        "seed": args.seed,
        "total": total,
        "ok": len(ok),
        "fail": len(fail),
        "rps_achieved": total / elapsed if elapsed else 0.0,
        "status_hist": status_hist,
        "err_hist": dict(list(err_hist.items())[:10]),
        "latency_ms": pcts(lat) if lat else {},
        "response_bytes_mean": (statistics.mean(r.bytes for r in ok) if ok else 0.0),
    }
    if args.rps:
        report["scheduler"] = {
            "scheduled": loop_stats.scheduled,
            "dropped": loop_stats.dropped,
            "in_flight_peak": loop_stats.in_flight_peak,
        }
    if args.bucket_seconds:
        report["buckets"] = _bucket_stats(results, args.bucket_seconds, elapsed)
    return report


def _print_report(report: dict) -> None:
    print(f"mode: {report['mode']}  url: {report['url']}")
    print(
        f"requests: total={report['total']} ok={report['ok']} fail={report['fail']}  "
        f"rps_achieved={report['rps_achieved']:.2f}"
    )
    if report["fail"]:
        print(f"failures by status: {report['status_hist']}")
        if report["err_hist"]:
            print(f"failures by exception: {report['err_hist']}")
    if report.get("scheduler"):
        sch = report["scheduler"]
        print(
            f"scheduler: scheduled={sch['scheduled']} dropped={sch['dropped']} "
            f"in_flight_peak={sch['in_flight_peak']}"
        )
    st = report["latency_ms"]
    if st:
        print(
            f"latency ms: p50={st['p50']:.0f}  p90={st['p90']:.0f}  "
            f"p95={st['p95']:.0f}  p99={st['p99']:.0f}  max={st['max']:.0f}  "
            f"mean={st['mean']:.0f}"
        )
    if report["ok"]:
        print(f"response bytes (mean): {report['response_bytes_mean']:.0f}")
    if "buckets" in report:
        print("\nper-bucket (latency is ok-only):")
        print(
            f"  {'t0_s':>6} {'n':>5} {'ok':>5} {'fail':>5} {'rps':>6} "
            f"{'p50':>6} {'p95':>6} {'p99':>6} {'max':>6}"
        )
        for b in report["buckets"]:
            if b["n"] == 0:
                print(f"  {b['bucket_s']:>6.0f} {'-':>5}")
                continue
            print(
                f"  {b['bucket_s']:>6.0f} {b['n']:>5} {b['ok']:>5} {b['fail']:>5} "
                f"{b['rps']:>6.1f} {b['p50']:>6.0f} {b['p95']:>6.0f} "
                f"{b['p99']:>6.0f} {b['max']:>6.0f}"
            )


# --- slow-read attack -------------------------------------------------------
# Reproduces the observed prod failure: attacker connects, sends a normal
# request, then drip-reads the response at bytes_per_sec rate. gthread workers
# park in wsgi.write() while kernel send buffer is full, exhausting the thread
# pool without ever using meaningful CPU on the server.


async def _slow_reader(
    host: str,
    port: int,
    path: str,
    rng: random.Random,
    read_bps: int,
    chunk: int,
    deadline: float,
    test_start: float,
    stats: dict,
) -> None:
    while time.perf_counter() < deadline:
        cmd = gen_bot_cmd(rng)
        import urllib.parse as _up

        qs = _up.urlencode({"cmd": cmd})
        req = (
            f"GET {path}?{qs} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"User-Agent: loadtest-slowread\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n\r\n"
        )
        t0 = time.perf_counter()
        reader = writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
            writer.write(req.encode("ascii"))
            await writer.drain()
            stats["opened"] += 1
            sleep_s = max(0.001, chunk / max(1, read_bps))
            bytes_read = 0
            while time.perf_counter() < deadline:
                try:
                    data = await asyncio.wait_for(reader.read(chunk), timeout=30)
                except asyncio.TimeoutError:
                    stats["read_timeouts"] += 1
                    break
                if not data:
                    break
                bytes_read += len(data)
                await asyncio.sleep(sleep_s)
            stats["completed"] += 1
            stats["total_bytes"] += bytes_read
            stats["durations_s"].append(time.perf_counter() - t0)
        except Exception as e:
            stats["errors"] += 1
            stats["err_hist"][type(e).__name__] = (
                stats["err_hist"].get(type(e).__name__, 0) + 1
            )
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass


async def _witness(
    url: str,
    rng: random.Random,
    deadline: float,
    interval_s: float,
    witness_results: list[Result],
    test_start: float,
) -> None:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=120)
    connector = aiohttp.TCPConnector(limit=4, force_close=True)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            status = 0
            err = ""
            nbytes = 0
            try:
                async with session.get(
                    f"{url}/", timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    status = resp.status
                    body = await resp.read()
                    nbytes = len(body)
                    ok = 200 <= status < 400
            except Exception as e:
                ok = False
                err = f"{type(e).__name__}: {e}"[:120]
            latency_ms = (time.perf_counter() - t0) * 1000.0
            witness_results.append(
                Result(
                    ok=ok,
                    status=status,
                    latency_ms=latency_ms,
                    bytes=nbytes,
                    t_end_s=time.perf_counter() - test_start,
                    err=err,
                )
            )
            await asyncio.sleep(max(0.0, interval_s - (time.perf_counter() - t0)))


async def run_slowread(args: argparse.Namespace) -> dict:
    import urllib.parse as _up

    u = _up.urlparse(args.url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    path = u.path or "/explain"
    if not path.endswith("/explain"):
        path = path.rstrip("/") + "/explain"

    rng = random.Random(args.seed)
    test_start = time.perf_counter()
    deadline = test_start + args.duration

    stats = {
        "opened": 0,
        "completed": 0,
        "errors": 0,
        "read_timeouts": 0,
        "total_bytes": 0,
        "durations_s": [],
        "err_hist": {},
    }
    witness_results: list[Result] = []

    tasks = [
        asyncio.create_task(
            _slow_reader(
                host,
                port,
                path,
                random.Random(args.seed + i),
                args.read_bps,
                args.chunk,
                deadline,
                test_start,
                stats,
            )
        )
        for i in range(args.concurrency)
    ]
    if args.witness_interval > 0:
        tasks.append(
            asyncio.create_task(
                _witness(
                    args.url,
                    rng,
                    deadline,
                    args.witness_interval,
                    witness_results,
                    test_start,
                )
            )
        )
    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.perf_counter() - test_start
    durs = stats.pop("durations_s")
    w_lat = [r.latency_ms for r in witness_results if r.ok]
    w_fail = [r for r in witness_results if not r.ok]
    return {
        "mode": "slowread",
        "url": args.url,
        "duration_s": args.duration,
        "concurrency": args.concurrency,
        "read_bps": args.read_bps,
        "chunk": args.chunk,
        "elapsed_s": elapsed,
        "attackers": {
            **stats,
            "duration_s": pcts(durs) if durs else {},
        },
        "witness": {
            "interval_s": args.witness_interval,
            "total": len(witness_results),
            "ok": len(witness_results) - len(w_fail),
            "fail": len(w_fail),
            "latency_ms": pcts(w_lat) if w_lat else {},
            "err_hist": {
                k: sum(1 for r in w_fail if r.err.startswith(k))
                for k in sorted({r.err.split(":")[0] for r in w_fail if r.err})
            },
            "status_hist": {
                str(s): sum(1 for r in witness_results if r.status == s)
                for s in sorted({r.status for r in witness_results if r.status})
            },
        },
    }


# --- in-process profile -----------------------------------------------------


def run_profile(args: argparse.Namespace) -> dict:
    import os

    os.environ["DB_PATH"] = args.db
    from explainshell.store import Store
    from explainshell.web.views import explain_cmd

    store = Store(args.db)

    rng = random.Random(args.seed)
    cmds = [gen_bot_cmd(rng) for _ in range(args.n)]
    distro_preference = [("ubuntu", "26.04"), ("arch", "rolling")]

    for c in cmds[:3]:
        try:
            explain_cmd(c, store, distro_preference=distro_preference)
        except Exception as e:
            print(f"warmup error: {type(e).__name__}: {e}")

    pr = cProfile.Profile()
    per_call: list[float] = []
    errors = 0
    pr.enable()
    for c in cmds:
        t0 = time.perf_counter()
        try:
            explain_cmd(c, store, distro_preference=distro_preference)
            per_call.append((time.perf_counter() - t0) * 1000.0)
        except Exception:
            errors += 1
    pr.disable()

    st = pcts(per_call)
    print(f"explain_cmd calls: n={len(per_call)} errors={errors}")
    if st:
        print(
            f"wall ms: p50={st['p50']:.1f}  p95={st['p95']:.1f}  "
            f"p99={st['p99']:.1f}  max={st['max']:.1f}  mean={st['mean']:.1f}"
        )

    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(40)
    print("\n=== cProfile top (cumulative) ===\n")
    print(s.getvalue())

    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(30)
    print("\n=== cProfile top (self time) ===\n")
    print(s.getvalue())

    return {"n": len(per_call), "errors": errors, "latency_ms": st}


# --- CLI --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    h = sub.add_parser("http")
    h.add_argument("--url", default="http://127.0.0.1:5000")
    h.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=8,
        help="closed-loop: worker count; open-loop: in-flight cap",
    )
    h.add_argument("-d", "--duration", type=int, default=30)
    h.add_argument("--seed", type=int, default=42)
    h.add_argument(
        "--rps",
        type=float,
        default=0.0,
        help="target arrival rate (open-loop). 0 = closed-loop (fixed concurrency)",
    )
    h.add_argument(
        "--simple-ratio",
        type=float,
        default=0.0,
        help="fraction of requests that are simple (non-bot) commands",
    )
    h.add_argument(
        "--bucket-seconds",
        type=float,
        default=0.0,
        help="if > 0, emit per-bucket latency rows (p50/p95/p99) over test duration",
    )
    h.add_argument(
        "--json",
        action="store_true",
        help="emit JSON report to stdout instead of human-readable text",
    )

    p = sub.add_parser("profile")
    p.add_argument("--db", default="explainshell.db")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--seed", type=int, default=42)

    sr = sub.add_parser("slowread", help="slow-read attack (park gthread workers)")
    sr.add_argument("--url", default="http://127.0.0.1:5000")
    sr.add_argument("-c", "--concurrency", type=int, default=16)
    sr.add_argument("-d", "--duration", type=int, default=60)
    sr.add_argument("--seed", type=int, default=42)
    sr.add_argument(
        "--read-bps", type=int, default=512, help="per-connection read rate (bytes/s)"
    )
    sr.add_argument("--chunk", type=int, default=128, help="read chunk size (bytes)")
    sr.add_argument(
        "--witness-interval",
        type=float,
        default=2.0,
        help="seconds between witness GET / probes (0 disables)",
    )

    args = ap.parse_args()

    if args.mode == "http":
        report = asyncio.run(run_http(args))
        if args.json:
            print(_json.dumps(report, default=str, indent=2))
        else:
            _print_report(report)
    elif args.mode == "slowread":
        report = asyncio.run(run_slowread(args))
        print(_json.dumps(report, default=str, indent=2))
    else:
        run_profile(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
