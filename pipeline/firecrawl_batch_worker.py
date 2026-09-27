"""Durable public Firecrawl batches sharing firecrawl_worker.Queue and worker.lock.

Contracts verified by the root's official-doc review and saved five-page probe.
This module never reads credentials or makes requests until main/run is invoked.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import firecrawl_worker as legacy

API = "https://api.firecrawl.dev/v2/"
TERMINAL = {"completed", "failed", "cancelled"}


class SelectionError(ValueError):
    pass


def read_selection(path):
    """Exact URLs in global input order; duplicate URLs keep their first slot."""
    path = Path(path).resolve()
    content = path.read_text(encoding="utf-8-sig")
    try:
        if path.suffix.lower() == ".jsonl":
            values = [json.loads(line) for line in content.splitlines() if line.strip()]
        else:
            values = json.loads(content)
            if isinstance(values, dict):
                values = values.get("urls")
        if not isinstance(values, list):
            raise SelectionError("Selection must be a JSON array, {urls: [...]}, or JSONL values")
    except json.JSONDecodeError as error:
        raise SelectionError("Selection file is not valid JSON/JSONL") from error
    urls, categories, seen = [], [], set()
    for index, value in enumerate(values, 1):
        url = value.get("url") if isinstance(value, dict) else value
        if not isinstance(url, str):
            raise SelectionError("Selection entry " + str(index) + " must contain a URL string")
        parsed = urllib.parse.urlsplit(url)
        category = legacy.category(url)
        if (url != url.strip() or parsed.scheme != "https" or parsed.netloc != "trellis.law"
                or parsed.fragment or not legacy.allowed(url) or not category or category[0] not in {"county", "rules", "judge_profile", "judge_directory"}):
            raise SelectionError("Selection entry " + str(index) + " is not an allowed exact county/rules/judge URL")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)  # Do not canonicalize or add a frontier row from a selector.
        if not categories or categories[-1] != category[0]:
            categories.append(category[0])
    return {"path": str(path), "urls": urls, "category_order": categories,
            "sha256": hashlib.sha256(json.dumps(urls, separators=(",", ":")).encode("utf-8")).hexdigest()}


def job_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid provider job ID")
    return value


def status_path(value, identity):
    """Only the exact job status path and numeric skip pagination are trusted."""
    expected = "/v2/batch/scrape/" + job_id(identity)
    parsed = urllib.parse.urlsplit(value)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.netloc != "api.firecrawl.dev" or parsed.path != expected
            or parsed.fragment or len(pairs) > 1 or any(key != "skip" or not val.isdigit() for key, val in pairs)):
        raise ValueError("Provider pagination URL is outside the exact API job scope")
    return parsed.path.removeprefix("/v2/") + ("?" + parsed.query if parsed.query else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("API redirects are disabled")


class Client:
    def __init__(self, key):
        self.key = key
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def call(self, method, path, payload=None):
        if path == "team/credit-usage":
            valid = method == "GET"
        elif path == "batch/scrape":
            valid = method == "POST"
        else:
            match = re.fullmatch(r"batch/scrape/([a-f0-9-]{36})(/errors)?(?:\?skip=(\d+))?", path)
            valid = bool(match) and method in {"GET", "DELETE"}
            if valid:
                job_id(match[1])
                valid = not (method == "DELETE" and (match[2] or match[3])) and not (match[2] and match[3])
        if not valid:
            raise ValueError("Invalid API operation")
        request = urllib.request.Request(API + path, method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=95) as response:
                status, raw = response.status, response.read(80 * 1024 * 1024 + 1)
                if len(raw) > 80 * 1024 * 1024:
                    raise ValueError("Provider response exceeded the archive bound")
                body = json.loads(raw.decode().replace(self.key, "[REDACTED]"))
        except urllib.error.HTTPError as error:
            raw = error.read(1024 * 1024).decode(errors="replace").replace(self.key, "[REDACTED]")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"success": False, "error": raw[:1000]}
            body["_collector_transport"] = {"retry_after": error.headers.get("Retry-After")}
            status = error.code
        if not isinstance(body, dict):
            raise ValueError("Provider returned a non-object response")
        return status, body


@contextlib.contextmanager
def worker_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    stream = (state / "worker.lock").open("a+b")
    stream.seek(0, 2)
    if not stream.tell():
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            raise RuntimeError("A Firecrawl worker is already active") from error
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class BatchWorker:
    def __init__(self, client, base=None, state=None, batch_size=20, reserve=1, min_free=10 * 1024**3, clock=time.time,
                 cache_max_age_ms=0, page_timeout_ms=60000, selection_file=None):
        self.selection = read_selection(selection_file) if selection_file is not None else None
        self.client, self.base, self.state = client, Path(base or legacy.BASE), Path(state or legacy.STATE)
        self.state.mkdir(parents=True, exist_ok=True)
        self.batch_size, self.reserve, self.min_free, self.clock = min(100, max(1, batch_size)), max(1, reserve), min_free, clock
        if not 0 <= cache_max_age_ms <= 172800000 or not 1000 <= page_timeout_ms <= 300000:
            raise ValueError("Cache age or page timeout is outside the supported archive bounds")
        self.cache_max_age_ms, self.page_timeout_ms = cache_max_age_ms, page_timeout_ms
        self.q = legacy.Queue(self.state / "frontier.sqlite3")
        self.q.db.execute("CREATE TABLE IF NOT EXISTS batch_items(local_job TEXT,url TEXT,attempt_id INTEGER,PRIMARY KEY(local_job,url))")
        if self.selection is not None:
            self.q.db.execute("CREATE TEMP TABLE batch_selection(url TEXT PRIMARY KEY,ordinal INTEGER,category TEXT,priority INTEGER)")
            self.q.db.executemany("INSERT INTO batch_selection VALUES(?,?,?,?)", [
                (url, index, *legacy.category(url)[:2])
                for index, url in enumerate(self.selection["urls"])])
        self.q.db.commit()
        self.active_file = self.state / "batch_active.json"
        self.rate_file = self.state / "batch_rate_limit.json"
        self.job = None
        self.credits = None
        self.run_used = 0
        self.reason = "running"
        self.rate = json.loads(self.rate_file.read_text(encoding="utf-8")) if self.rate_file.exists() else {}
        prior = json.loads((self.state / "status.json").read_text(encoding="utf-8")) if (self.state / "status.json").exists() else {}
        self.rate["next_post_at"] = max(self.rate.get("next_post_at", 0), prior.get("cooldown_until_epoch") or 0)

    @contextlib.contextmanager
    def paths(self):
        old = legacy.BASE, legacy.STATE
        legacy.BASE, legacy.STATE = self.base, self.state
        try:
            yield
        finally:
            legacy.BASE, legacy.STATE = old

    def save_job(self):
        self.job["updated_at"] = legacy.now()
        legacy.save(self.job_path(), self.job)

    def job_path(self):
        return self.state / "batch_jobs" / self.job["local_id"] / "manifest.json"

    def raw(self, label, status, body):
        path = self.job_path().parent / (label + ".json")
        legacy.save(path, {"saved_at": legacy.now(), "api_status": status, "response": body})
        return path

    def selected_pending(self, limit=1):
        return [dict(row) for row in self.q.db.execute(
            "SELECT f.* FROM batch_selection s JOIN frontier f ON f.url=s.url "
            "WHERE f.status='pending' AND f.in_scope=1 AND f.category=s.category AND f.priority=s.priority "
            "ORDER BY s.ordinal LIMIT ?", (limit,))]

    def selection_identity(self):
        return None if self.selection is None else {key: self.selection[key] for key in ("path", "sha256", "category_order")} | {"url_count": len(self.selection["urls"]), "order_policy": "global_url_order_contiguous_same_category"}

    def check_job_selection(self):
        stored = self.job.get("selection")
        if self.selection is None:
            if stored is not None:
                raise SelectionError("Active selected batch requires its original --selection-file")
            return
        if stored is None or stored.get("sha256") != self.selection["sha256"]:
            raise SelectionError("Active batch and CLI selection identities differ")
        selected = set(self.selection["urls"])
        if any(row["url"] not in selected or row["category"] not in {"county", "rules", "judge_profile", "judge_directory"} for row in self.job["rows"]):
            raise SelectionError("Prepared batch contains a row outside its selection")

    def load(self):
        if self.active_file.exists():
            pointer = json.loads(self.active_file.read_text(encoding="utf-8"))
            local_id = str(uuid.UUID(pointer["local_id"]))
            self.job = json.loads((self.state / "batch_jobs" / local_id / "manifest.json").read_text(encoding="utf-8"))
            if self.job["local_id"] != local_id:
                raise ValueError("Batch manifest identity mismatch")
            self.check_job_selection()
        with self.paths():
            self.q.import_saved()
        if self.job:
            # The base importer can recover old single-page claims. Batch rows
            # retain their distinct hold state until this manifest resolves.
            self.q.db.executemany("UPDATE frontier SET status='batch_held' WHERE url=? AND status NOT IN ('downloaded','access_blocked','captured_elsewhere')", [(r["url"],) for r in self.job["rows"]])
            self.q.db.commit()
            response = self.job_path().parent / ("submission_" + str(self.job.get("post_sequence", 1)) + ".json")
            if not self.job.get("job_id") and response.exists():
                saved = json.loads(response.read_text(encoding="utf-8"))
                if saved["api_status"] == 200 and saved["response"].get("success") and saved["response"].get("id"):
                    self.accept_submission(saved["response"])
            if self.job["state"] == "submitting" and not self.job.get("job_id"):
                self.job["state"] = "uncertain_submission"
                self.save_job()
            for filename in self.job.get("poll_files", []):
                saved = json.loads((self.job_path().parent / filename).read_text(encoding="utf-8"))
                self.consume(saved["response"], self.job_path().parent / filename)

    def preflight(self):
        status, body = self.client.call("GET", "team/credit-usage")
        legacy.save(self.state / "batch_credit_preflight.json", {"checked_at": legacy.now(), "api_status": status, "response": body})
        value = body.get("data", {}).get("remainingCredits") if status == 200 else None
        self.credits = value
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= self.reserve:
            self.reason = "paused_credit_or_auth"
            return False
        return True

    def prepare(self):
        limit = min(self.batch_size, max(0, int(self.credits - self.reserve)))
        selected = self.selected_pending(limit) if self.selection is not None else None
        phase = (selected[0]["priority"] if selected else None) if selected is not None else self.q.db.execute("SELECT min(priority) FROM frontier WHERE status='pending' AND in_scope=1").fetchone()[0]
        if phase is None:
            self.reason = "selection_exhausted_or_not_pending" if selected is not None else "frontier_exhausted_or_failed"
            return False
        self.job = {"local_id": str(uuid.uuid4()), "state": "preparing", "created_at": legacy.now(), "phase_priority": phase,
            "rows": [], "poll_files": [], "credits_used_observed": 0, "source_category": "public_provider_extract",
            "scrape_options": {"formats": ["markdown", "html", "links"], "maxConcurrency": 2,
                "proxy": "basic", "maxAge": self.cache_max_age_ms, "timeout": self.page_timeout_ms,
                "skipTlsVerification": False}}
        if selected is not None:
            self.job.update(selection=self.selection_identity(), phase_category=selected[0]["category"])
        self.save_job()
        legacy.save(self.active_file, {"local_id": self.job["local_id"]})
        for index in range(limit):
            if selected is None:
                next_phase = self.q.db.execute("SELECT min(priority) FROM frontier WHERE status='pending' AND in_scope=1").fetchone()[0]
                if next_phase != phase:
                    break
                row = self.q.claim()
            else:
                if index >= len(selected) or selected[index]["category"] != self.job["phase_category"] or selected[index]["priority"] != phase:
                    break
                row = selected[index]
                claimed = self.q.db.execute("UPDATE frontier SET status='fetching',attempts=attempts+1 WHERE url=? AND status='pending' AND in_scope=1", (row["url"],)).rowcount
                self.q.db.commit()
                if not claimed:
                    continue
            if not row:
                break
            self.job["rows"].append(row)
            self.save_job()
            self.q.db.execute("UPDATE frontier SET status='batch_held' WHERE url=?", (row["url"],))
            self.q.db.commit()
        self.job["state"] = "prepared"
        self.save_job()
        return bool(self.job["rows"])

    def accept_submission(self, body):
        identity = job_id(body["id"])
        if body.get("url"):
            status_path(body["url"], identity)
        self.job.update(state="submitted", job_id=identity, provider_status="scraping")
        self.save_job()

    def submit(self):
        self.check_job_selection()
        if self.clock() < self.rate.get("next_post_at", 0):
            self.reason = "rate_limit_cooldown"
            return False
        if self.job["state"] not in {"prepared", "rejected_rate_limit", "preparing"}:
            self.reason = "paused_" + self.job["state"]
            return False
        if not self.job["rows"] or any(not legacy.allowed(r["url"]) or legacy.category(r["url"])[1] != self.job["phase_priority"] for r in self.job["rows"]):
            self.reason = "paused_scope_mismatch"
            return False
        if self.selection is not None and any(self.q.db.execute(
                "SELECT status FROM frontier WHERE url=?", (row["url"],)).fetchone()[0] in {"downloaded", "captured_elsewhere", "access_blocked"}
                for row in self.job["rows"]):
            # Keep the prepared request identity intact instead of silently
            # charging again or changing a durable manifest after a restart.
            self.reason = "paused_prepared_capture_or_access_conflict"
            return False
        if self.credits is None or self.credits < len(self.job["rows"]) + self.reserve:
            self.reason = "paused_credit_reserve"
            return False
        # Prepared jobs retain their original options across restarts; legacy jobs
        # retain the original fresh-only request contract.
        options = self.job.get("scrape_options", {"formats": ["markdown", "html", "links"], "maxConcurrency": 2,
            "proxy": "basic", "maxAge": 0, "timeout": 60000, "skipTlsVerification": False})
        payload = {**options, "urls": [r["url"] for r in self.job["rows"]]}
        self.raw("request", 0, payload)
        self.job["state"] = "submitting"
        self.job["post_sequence"] = self.job.get("post_sequence", 0) + 1
        self.save_job()  # A crash or timeout from this point may have created a paid remote job.
        self.rate.update(last_post_at=self.clock(), next_post_at=self.clock() + 31)
        legacy.save(self.rate_file, self.rate)
        try:
            status, body = self.client.call("POST", "batch/scrape", payload)
        except Exception as error:
            self.raw("submission_exception", 0, {"error_type": type(error).__name__})
            self.job["state"] = "uncertain_submission"
            self.save_job()
            self.reason = "paused_uncertain_submission"
            return False
        self.raw("submission_" + str(self.job["post_sequence"]), status, body)
        if status == 200 and body.get("success") and body.get("id"):
            self.accept_submission(body)
            return True
        if status == 429 and not re.search(r"credit|quota|payment", str(body.get("error", "")), re.I):
            self.rate["next_post_at"] = max(self.rate["next_post_at"], self.clock() + legacy.retry_delay(body, self.clock()))
            legacy.save(self.rate_file, self.rate)
            self.job["state"], self.reason = "rejected_rate_limit", "rate_limit_cooldown"
        else:
            self.job["state"] = "rejected_provider" if status in {400, 401, 402, 403, 422, 429} else "uncertain_submission"
            self.reason = "paused_" + self.job["state"]
        self.save_job()
        return False

    def consume(self, body, rawfile):
        cost = body.get("creditsUsed")
        if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0:
            old = self.job.get("credits_used_observed", 0)
            self.run_used += max(0, cost - old)
            self.job["credits_used_observed"] = max(old, cost)
        requested = {r["url"]: r for r in self.job["rows"]}
        for data in body.get("data") or []:
            if not isinstance(data, dict):
                raise ValueError("Invalid batch result record")
            meta = data.get("metadata") or {}
            url = legacy.canonical_url(meta.get("sourceURL") or meta.get("url", ""))
            if url not in requested:
                self.job["unassociated_results"] = True
                continue
            if self.q.db.execute("SELECT 1 FROM batch_items WHERE local_job=? AND url=?", (self.job["local_id"], url)).fetchone():
                continue
            target = int(meta.get("statusCode") or 0)
            challenge = re.search(r"just a moment|attention required|access denied", str(meta.get("title", "")), re.I) or re.search(r"verify you are human|checking your browser", str(data.get("markdown", ""))[:1500], re.I)
            barrier = target in {401, 403, 429} or bool(challenge)
            result = "access_blocked" if barrier else "downloaded" if 200 <= target < 300 and (data.get("markdown") or data.get("html")) else "provider_error"
            output = rawfile
            if result == "downloaded":
                row = requested[url]
                category = row["category"]
                folder = "counties" if category == "county" else "rules" if category == "rules" else "judges" if category.startswith("judge") else category
                parts = urllib.parse.urlsplit(url).path.strip("/").split("/")
                name = "_".join(parts[1:]) if category == "county" and not urllib.parse.urlsplit(url).query else hashlib.sha256(url.encode()).hexdigest()
                output = self.base / folder / (name + ".firecrawl.json")
                legacy.save(output, data)
                self.q.import_data(data, output)
            self.q.db.execute("UPDATE frontier SET status=?,response_path=?,error=? WHERE url=?", (result, str(output), None if result == "downloaded" else "Batch target status " + str(target), url))
            page_cost = meta.get("creditsUsed")
            if not isinstance(page_cost, int) or isinstance(page_cost, bool):
                page_cost = None  # Never infer page charges when only the aggregate is reported.
            cursor = self.q.db.execute("INSERT INTO attempts(url,attempted_at,api_status,target_status,result_status,credits_used,response_path) VALUES(?,?,?,?,?,?,?)", (url, legacy.now(), 200, target, result, page_cost, str(rawfile)))
            self.q.db.execute("INSERT INTO batch_items VALUES(?,?,?)", (self.job["local_id"], url, cursor.lastrowid))
            self.q.db.commit()
            if barrier:
                self.job["target_barrier"] = True
        self.save_job()

    def cancel(self, reason):
        self.job["state"] = "cancel_requested"
        self.job["halt_reason"] = reason
        self.save_job()
        try:
            status, body = self.client.call("DELETE", "batch/scrape/" + job_id(self.job["job_id"]))
            self.raw("cancellation", status, body)
            self.job["cancel_acknowledged"] = status == 200 and bool(body.get("success"))
        except Exception as error:
            self.raw("cancellation_exception", 0, {"error_type": type(error).__name__})
            self.job["cancel_acknowledged"] = False
        self.save_job()
        self.reason = reason

    def poll(self):
        path = "batch/scrape/" + job_id(self.job["job_id"])
        visited = set()
        terminal = None
        while path:
            if path in visited or len(visited) >= 10000:
                raise ValueError("Repeated or excessive provider pagination")
            visited.add(path)
            status, body = self.client.call("GET", path)
            rawfile = self.raw("poll_" + str(len(self.job["poll_files"])), status, body)
            self.job["poll_files"].append(rawfile.name)
            self.save_job()
            if status != 200 or not body.get("success"):
                if status == 429:
                    self.rate["poll_after"] = self.clock() + legacy.retry_delay(body, self.clock())
                    self.rate["next_post_at"] = max(self.rate.get("next_post_at", 0), self.rate["poll_after"])
                    legacy.save(self.rate_file, self.rate)
                    self.reason = "rate_limit_cooldown"
                else:
                    self.reason = "paused_poll_provider_error"
                return False
            self.consume(body, rawfile)
            self.job["provider_status"] = body.get("status")
            terminal = body.get("status") if body.get("status") in TERMINAL else terminal
            self.save_job()
            if self.job.get("target_barrier") and terminal is None:
                self.cancel("paused_target_access")
                return False
            next_path = status_path(body["next"], self.job["job_id"]) if body.get("next") else None
            # While a job is running, Firecrawl can publish ?skip=0 before
            # any page exists, or repeat the current cursor as a pending tail.
            # Validate it first, then yield to run()'s timed polling instead of
            # issuing immediate repeated GETs or treating pending work as an
            # API contract failure. Terminal cursor loops remain errors.
            if body.get("status") == "scraping" and terminal is None and (not body.get("data") or next_path in visited):
                self.reason = "running"
                return False
            path = next_path
        if terminal:
            found = self.q.db.execute("SELECT count(*) FROM batch_items WHERE local_job=?", (self.job["local_id"],)).fetchone()[0]
            self.job["state"] = terminal
            if found != len(self.job["rows"]) or self.job.get("unassociated_results"):
                status, errors = self.client.call("GET", "batch/scrape/" + self.job["job_id"] + "/errors")
                self.raw("errors", status, errors)
                self.job["state"] = "terminal_unresolved"
                self.reason = "paused_batch_unresolved"
            elif self.job.get("target_barrier") or self.job.get("halt_reason"):
                self.reason = self.job.get("halt_reason", "paused_target_access")
            elif terminal == "completed":
                self.job["state"] = "archived"
                self.save_job()
                self.active_file.unlink()
                self.job = None
                return True
            else:
                self.reason = "paused_batch_" + terminal
            self.save_job()
            return False
        return False

    def summary(self):
        self.q.cooldown_until = max(self.rate.get("next_post_at", 0), self.rate.get("poll_after", 0))
        with self.paths():
            result = self.q.summary(self.reason, self.credits, self.run_used)
        result.update(worker_kind="batch", batch_size=self.batch_size, max_concurrency=2,
            cache_max_age_ms=self.cache_max_age_ms, page_timeout_ms=self.page_timeout_ms,
            active_batch=None if self.job is None else {key: self.job.get(key) for key in ("local_id", "job_id", "state", "phase_priority", "credits_used_observed")},
            credit_accounting="Cumulative provider job credits counted once; per-page attempt cost is null if the provider omitted it")
        if self.selection is not None:
            upcoming = self.selected_pending()
            result.update(selection=self.selection_identity(), phase_order=self.selection["category_order"],
                active_phase=self.job.get("phase_category") if self.job else upcoming[0]["category"] if upcoming else None,
                selected_status_counts={row[0]: row[1] for row in self.q.db.execute(
                    "SELECT coalesce(f.status,'not_in_frontier'),count(*) FROM batch_selection s LEFT JOIN frontier f ON f.url=s.url GROUP BY 1")},
                selected_eligible_pending=self.q.db.execute(
                    "SELECT count(*) FROM batch_selection s JOIN frontier f ON f.url=s.url WHERE f.status='pending' AND f.in_scope=1 AND f.category=s.category AND f.priority=s.priority").fetchone()[0])
            legacy.save(self.state / "status.json", result)
        legacy.save(self.state / "batch_status.json", result)
        return result

    def run(self, once=False, poll_seconds=5):
        try:
            self.load()
            while True:
                if shutil.disk_usage(self.base).free < self.min_free:
                    self.reason = "paused_disk_guard"
                    if self.job and self.job.get("job_id"):
                        self.cancel(self.reason)
                    break
                if (self.state / "STOP").exists():
                    self.reason = "operator_checkpoint"
                    if self.job and self.job.get("job_id"):
                        self.cancel(self.reason)
                    break
                if self.job and self.job.get("job_id"):
                    if self.job.get("target_barrier") and not self.job.get("cancel_acknowledged"):
                        self.cancel("paused_target_access")
                        break
                    if self.clock() < self.rate.get("poll_after", 0):
                        self.reason = "rate_limit_cooldown"
                    elif self.poll():
                        if once:
                            self.reason = "batch_complete"
                            break
                        continue
                    elif self.reason.startswith("paused_"):
                        break
                else:
                    if self.job and self.job["state"] not in {"prepared", "preparing", "rejected_rate_limit"}:
                        self.reason = "paused_" + self.job["state"]
                        break
                    if self.job is None and self.selection is not None and not self.selected_pending():
                        self.reason = "selection_exhausted_or_not_pending"
                        break
                    if self.clock() >= self.rate.get("next_post_at", 0):
                        if not self.preflight():
                            break
                        if self.job and self.job["state"] == "preparing":
                            if self.job["rows"]:
                                self.job["state"] = "prepared"
                                self.save_job()
                            else:
                                self.job["state"] = "aborted_preparation"
                                self.save_job()
                                self.active_file.unlink()
                                self.job = None
                        if self.job is None and not self.prepare():
                            break
                        if self.submit():
                            self.reason = "running"
                            continue
                        if self.reason.startswith("paused_"):
                            break
                    else:
                        self.reason = "rate_limit_cooldown"
                self.summary()
                deadline = time.monotonic() + max(1, poll_seconds)
                while time.monotonic() < deadline and not (self.state / "STOP").exists():
                    time.sleep(min(1, deadline - time.monotonic()))
            return self.summary()
        except Exception as error:
            self.reason = "paused_selection_mismatch" if isinstance(error, SelectionError) else "paused_" + ("provider_contract" if isinstance(error, (ValueError, KeyError, TypeError)) else "transport_or_storage")
            if self.job:
                self.job["checkpoint_error_type"] = type(error).__name__
                self.job["checkpoint_error_winerror"] = getattr(error, "winerror", None)
                self.job["checkpoint_error_file_basename"] = Path(error.filename).name if getattr(error, "filename", None) else None
                self.save_job()
            return self.summary()
        finally:
            self.q.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once-batch", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--credit-reserve", type=int, default=1)
    parser.add_argument("--cache-max-age-ms", type=int, default=0)
    parser.add_argument("--page-timeout-ms", type=int, default=60000)
    parser.add_argument("--selection-file", type=Path, help="Ordered JSON array/{urls:[...]} or JSONL of exact county/rules/judge URLs (strings or {url:...}); only pending listed rows are claimed. Repeat the same selection to resume.")
    args = parser.parse_args(argv)
    with worker_lock(legacy.STATE):
        key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not key:
            raise RuntimeError("FIRECRAWL_API_KEY is missing")
        worker = BatchWorker(Client(key), batch_size=args.batch_size, reserve=args.credit_reserve,
                             cache_max_age_ms=args.cache_max_age_ms, page_timeout_ms=args.page_timeout_ms,
                             selection_file=args.selection_file)
        worker.run(once=args.once_batch or not args.run, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
