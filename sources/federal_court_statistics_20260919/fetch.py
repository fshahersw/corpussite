"""Bounded network packet for www.uscourts.gov statistics files.

Usage: python fetch.py --packet A|B
Rules enforced here: exact frozen URLs from seeds.jsonl, one host, one worker,
>= 3 s spacing coordinated through corpus/_shared_hosts, robots.txt honoured,
no retries, redirects followed manually (same host only, paced, receipted),
streamed to disk with a 40 MB per-file cap, packet wall clock < 600 s.
Original bytes + SHA-256 + per-request receipt are kept. Nothing is re-fetched
once a seed has a saved, content-checked body.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HOST = "www.uscourts.gov"
UA = "LegalCorpusResearch/1.0"
DELAY = 3.0
CAP = 40 * 1024 * 1024
PACKET_SECONDS = 600
LAUNCH_CUTOFF = 560  # do not start a new request after this many seconds
HARD_STOP = 592
MAX_HOPS = 3
SOFT_FAIL = ("page not found", "access denied", "just a moment", "captcha", "request unsuccessful", "attention required")

sys.path.insert(0, str(ROOT))
from pipeline.corpus_crawler import PacingDeferred, SharedHosts  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_name(url):
    name = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or "index"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]


def classify(head, content_type):
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:2] == b"PK":
        return "xlsx"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    lowered = head[:2048].lower()
    if b"<html" in lowered or b"<!doctype html" in lowered or "html" in (content_type or ""):
        return "html"
    if "text/plain" in (content_type or ""):
        return "txt"
    return "unknown"


class Pacer:
    def __init__(self, shared):
        self.shared, self.last_end = shared, 0.0

    def wait(self, deadline):
        while True:
            pause = self.last_end + DELAY - time.time()
            if pause > 0:
                time.sleep(pause)
            try:
                self.shared.reserve(HOST, DELAY)
                return True
            except PacingDeferred as deferred:
                if deferred.until > deadline:
                    return False
                time.sleep(max(0.25, deferred.until - time.time()))

    def done(self):
        self.shared.release(HOST)
        self.last_end = time.time()


def request(url, target, started, log):
    """One HTTP GET, streamed. Returns a receipt dict (never raises)."""
    receipt = {"url": url, "requested_at": now_iso(), "status": None, "reason": None, "headers": {},
               "bytes": 0, "sha256": None, "location": None, "error": None}
    begun = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"})
        try:
            response = OPENER.open(req, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            receipt["status"], receipt["reason"] = response.status if hasattr(response, "status") else response.code, response.reason
            receipt["headers"] = {k: v for k, v in response.headers.items() if k.lower() not in ("set-cookie",)}
            if receipt["status"] in (301, 302, 303, 307, 308):
                receipt["location"] = response.headers.get("Location")
                return receipt
            digest, size = hashlib.sha256(), 0
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            with partial.open("wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > CAP:
                        receipt["error"] = "size_cap_exceeded_40MB"
                        break
                    if time.time() - started > HARD_STOP:
                        receipt["error"] = "packet_time_limit"
                        break
                    digest.update(chunk)
                    out.write(chunk)
            receipt["bytes"] = size
            if receipt["error"] or receipt["status"] != 200:
                partial.unlink(missing_ok=True)
                if not receipt["error"]:
                    receipt["error"] = "http_status"
                return receipt
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) != size:
                partial.unlink(missing_ok=True)
                receipt["error"] = "truncated_body"
                return receipt
            receipt["sha256"] = digest.hexdigest()
            partial.replace(target)
    except Exception as error:  # network failure: recorded, never retried
        receipt["error"] = type(error).__name__ + ": " + str(error)[:200]
    finally:
        receipt["elapsed_s"] = round(time.time() - begun, 3)
    return receipt


def content_check(path, content_type, listed_format):
    head = path.read_bytes()[:4096]
    kind = classify(head, content_type)
    problem = None
    if path.stat().st_size == 0:
        problem = "empty_body"
    elif kind == "html":
        text = path.read_bytes()[:200000].decode("utf-8", "replace").lower()
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
        title_text = title.group(1).strip() if title else ""
        if any(marker in title_text for marker in SOFT_FAIL):
            problem = "soft_failure_title:" + title_text[:80]
        elif listed_format in ("xlsx", "pdf"):
            problem = "html_returned_for_" + listed_format
    elif listed_format in ("xlsx", "pdf") and kind != listed_format:
        problem = "format_mismatch:" + kind
    return kind, problem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True, choices=["A", "B"])
    args = parser.parse_args()
    started = time.time()
    deadline = started + LAUNCH_CUTOFF
    seeds = [json.loads(line) for line in (HERE / "seeds.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    seeds = [s for s in seeds if s["packet"] == args.packet]
    seeds.sort(key=lambda s: (s.get("scout_priority") or 9))
    receipts_dir = HERE / "receipts"
    receipts_dir.mkdir(exist_ok=True)
    log_path = receipts_dir / f"packet_{args.packet}.jsonl"
    done = set()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("saved_path") and not row.get("content_problem"):
                done.add(row["seed_id"])
    shared = SharedHosts(ROOT / "corpus/_shared_hosts", HERE)
    pacer = Pacer(shared)
    requests_made = 0
    summary = {"packet": args.packet, "started_at": now_iso(), "saved": 0, "failed": 0, "skipped_already_saved": 0, "not_attempted": []}

    def record(row):
        with log_path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    # robots.txt: fetched once per day, kept as a raw receipt, consulted for every URL.
    robots_path = HERE / "raw/robots/robots.txt"
    if not robots_path.exists():
        if not pacer.wait(deadline):
            raise SystemExit("host busy")
        receipt = request(f"https://{HOST}/robots.txt", robots_path, started, None)
        pacer.done()
        requests_made += 1
        record({"seed_id": "robots", "hop": 0, **receipt, "saved_path": "raw/robots/robots.txt" if receipt["sha256"] else None})
        if not receipt["sha256"]:
            raise SystemExit("robots.txt unavailable; stopping (barrier)")
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(robots_path.read_text(encoding="utf-8", errors="replace").splitlines())
    crawl_delay = robots.crawl_delay(UA) or robots.crawl_delay("*")
    if crawl_delay and float(crawl_delay) > DELAY:
        raise SystemExit(f"robots crawl-delay {crawl_delay} exceeds planned spacing; re-plan")

    for seed in seeds:
        if seed["seed_id"] in done:
            summary["skipped_already_saved"] += 1
            continue
        if time.time() > deadline:
            summary["not_attempted"].append(seed["seed_id"])
            continue
        url, hop, chain = seed["url"], 0, []
        while True:
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname != HOST:
                record({"seed_id": seed["seed_id"], "hop": hop, "url": url, "error": "off_host_redirect_not_followed", "chain": chain})
                summary["failed"] += 1
                break
            if not robots.can_fetch(UA, url):
                record({"seed_id": seed["seed_id"], "hop": hop, "url": url, "error": "robots_disallow", "chain": chain})
                summary["failed"] += 1
                break
            if not pacer.wait(deadline):
                summary["not_attempted"].append(seed["seed_id"])
                break
            target = HERE / "raw" / args.packet / f"{seed['seed_id']}__{safe_name(url)}"
            receipt = request(url, target, started, None)
            pacer.done()
            requests_made += 1
            row = {"seed_id": seed["seed_id"], "hop": hop, "seed_url": seed["url"], **receipt, "chain": list(chain)}
            if receipt["location"] and hop < MAX_HOPS:
                record(row)
                chain.append({"url": url, "status": receipt["status"]})
                url, hop = urljoin(url, receipt["location"]), hop + 1
                continue
            if receipt["sha256"]:
                kind, problem = content_check(target, receipt["headers"].get("Content-Type"), seed.get("listed_format"))
                final = target
                if kind == "html" and not target.name.endswith(".html"):
                    final = target.with_name(target.name + ".html")
                    target.replace(final)
                row.update(saved_path=str(final.relative_to(HERE)).replace("\\", "/"), detected_format=kind,
                           content_problem=problem, final_url=url, captured_at=receipt["requested_at"])
                summary["saved" if not problem else "failed"] += 1
            else:
                summary["failed"] += 1
            record(row)
            break
        if requests_made >= 100:
            break
    summary.update(finished_at=now_iso(), elapsed_s=round(time.time() - started, 1), requests=requests_made)
    (receipts_dir / f"packet_{args.packet}_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
