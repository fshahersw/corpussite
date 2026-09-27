"""Functional tests: all HTTP traffic stays on a local fixture server."""
from __future__ import annotations

import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
from corpus_crawler import Artifacts, Config, Gate, Store, canonical_url, export_reports, run_crawl, run_lock


def simple_pdf() -> bytes:
    content = b"BT /F1 12 Tf 72 720 Td (Fixture PDF court record) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    data += f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return data


@contextlib.contextmanager
def fixture_server(routes):
    seen = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                seen.append((self.path, time.monotonic(), dict(self.headers)))
                occurrence = sum(path == self.path for path, _, _ in seen)
            value = routes.get(self.path, (404, {}, b"Not found"))
            status, headers, body = value(occurrence) if callable(value) else value
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def html(body, status=200, **headers):
    return status, {"Content-Type": "text/html; charset=utf-8", **headers}, body.encode()


class CorpusTests(unittest.TestCase):
    def setUp(self):
        scratch = PIPELINE / "tests" / "_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fixture-", dir=scratch)
        self.root = Path(self.temp.name).resolve()
        # Verify the recursive cleanup target is contained in this task's test workspace.
        self.assertTrue(self.root.is_relative_to(scratch.resolve()))
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()

    def create(self, base, seeds, **overrides):
        config = {"allow": [{"host": base.split("://", 1)[1], "path_prefixes": ["/"]}], "workers": 1, "per_host_delay": 0.015, "backoff_base_seconds": 0.02, "min_free_bytes": 0, "shared_host_dir": "", **overrides}
        cfg = Config.from_dict(config)
        store = Store(self.root, cfg)
        self.stores.append(store)
        artifacts = Artifacts(self.root, cfg)
        path = self.root / "seeds.jsonl"
        records = [{"url": base + seed, "source_family": "local_fixture", "jurisdiction": {"state": "test", "county": "test"}, "category": "rules", "discovered_from": "fixture"} if isinstance(seed, str) else seed for seed in seeds]
        path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        store.ingest(path)
        return store, artifacts

    def crawl(self, store, artifacts, pages=100, wait_retries=False):
        with run_lock(self.root):
            return run_crawl(store, artifacts, max_pages=pages, max_seconds=10, wait_for_retries=wait_retries)

    def test_resume_redirects_pdf_duplicates_and_graph(self):
        pdf = simple_pdf()
        doc = html("<title>Court Rule 1</title><main>Rule text with a court procedure.</main>")
        routes = {
            "/rules": html('<title>Rules</title><a href="/doc">Document</a><a href="/same">Same bytes</a><a href="/old">Old</a><a href="/record.pdf">PDF</a><a href="https://external.invalid/county">Official county</a><a href="/logout">Log out</a>'),
            "/doc": doc, "/same": doc,
            "/old": (302, {"Location": "/doc"}, b"Moved"),
            "/record.pdf": (200, {"Content-Type": "application/pdf", "Set-Cookie": "SECRET_COOKIE=not-to-store"}, pdf),
        }
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/rules"])
            first = self.crawl(store, artifacts, pages=1)
            self.assertEqual(first["downloaded_resources"], 1)
            self.assertGreater(first["pending_resources"], 0)
            first_id = store.db.execute("SELECT id FROM resources WHERE url=?", (base + "/rules",)).fetchone()[0]
            result = self.crawl(store, artifacts)
            self.assertEqual(result["pending_resources"], 0)
            self.assertEqual(result["statuses"], {"downloaded": 4, "redirect": 1})
            self.assertEqual(sum(path == "/rules" for path, _, _ in seen), 1)
            self.assertFalse(any(path == "/logout" for path, _, _ in seen))
            self.assertEqual(store.db.execute("SELECT count(*) FROM links WHERE source_id=?", (first_id,)).fetchone()[0], 6)
            self.assertEqual(result["duplicate_resources"], 1)
            rows = store.db.execute("SELECT raw_path FROM resources WHERE url IN (?,?)", (base + "/doc", base + "/same")).fetchall()
            self.assertEqual(rows[0][0], rows[1][0])
            pdf_row = store.db.execute("SELECT * FROM resources WHERE url=?", (base + "/record.pdf",)).fetchone()
            self.assertEqual((self.root / pdf_row["raw_path"]).read_bytes(), pdf)
            if importlib.util.find_spec("pypdf"):
                self.assertIn("Fixture PDF court record", (self.root / pdf_row["text_path"]).read_text(encoding="utf-8"))
            else:
                self.assertEqual(pdf_row["extraction_status"], "parser_unavailable")
            meta = (self.root / pdf_row["metadata_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET_COOKIE", meta)
            self.assertTrue(all("Cookie" not in headers and "Authorization" not in headers for _, _, headers in seen))
            exported = export_reports(store, artifacts)
            self.assertEqual(exported["rows"]["resources"], 5)
            self.assertTrue((self.root / "reports" / "resources.csv").exists())
            self.assertFalse(exported["summary"]["full_source_corpus_complete"])

    def test_forbidden_pauses_host_and_preserves_response(self):
        with fixture_server({"/restricted": html("Forbidden", 403), "/later": html("Must not be fetched")}) as (base, seen):
            store, artifacts = self.create(base, ["/restricted", "/later"])
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"forbidden": 1, "pending": 1})
            self.assertEqual(result["host_blocks_or_cooldowns"][0]["pause_reason"], "forbidden")
            self.assertFalse(any(path == "/later" for path, _, _ in seen))
            row = store.db.execute("SELECT raw_path FROM resources WHERE status='forbidden'").fetchone()
            self.assertIn(b"Forbidden", (self.root / row[0]).read_bytes())

    def test_login_redirect_is_explicit_and_not_followed(self):
        routes = {"/restricted": (302, {"Location": "/login"}, b""), "/login": html('<title>Sign in</title><input type="password">')}
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/restricted"])
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"login_required": 1})
            self.assertFalse(any(path == "/login" for path, _, _ in seen))
            edge = store.db.execute("SELECT target_url,relation FROM links").fetchone()
            self.assertEqual(tuple(edge), (base + "/login", "redirect"))

    def test_http_200_login_and_challenge_are_not_successful_corpus(self):
        for name, body, status in (
            ("login", '<title>Sign in</title><form><input type="password">Forgot password</form>', "login_required"),
            ("challenge", "<title>Just a moment...</title><main>Checking your browser</main>", "challenge"),
        ):
            with self.subTest(name=name), fixture_server({"/page": html(body)}) as (base, _):
                subroot = self.root / name
                cfg = Config.from_dict({"allow": [{"host": base.split("://")[1], "path_prefixes": ["/"]}], "respect_robots": False, "min_free_bytes": 0, "shared_host_dir": ""})
                store = Store(subroot, cfg)
                self.stores.append(store)
                path = subroot / "seeds.jsonl"
                path.write_text(json.dumps({"url": base + "/page"}), encoding="utf-8")
                store.ingest(path)
                with run_lock(subroot):
                    result = run_crawl(store, Artifacts(subroot, cfg), 5, 10)
                self.assertEqual(result["statuses"], {status: 1})
                self.assertEqual(result["downloaded_resources"], 0)

    def test_retry_after_defers_then_resumes(self):
        routes = {"/temporary": lambda n: (429, {"Retry-After": "1", "Content-Type": "text/plain"}, b"Slow down") if n == 1 else html("Recovered"), "/later": html("Later")}
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/temporary", "/later"])
            first = self.crawl(store, artifacts)
            self.assertEqual(first["statuses"], {"pending": 1, "retry_wait": 1})
            deadline = store.db.execute("SELECT next_attempt_at FROM resources WHERE status='retry_wait'").fetchone()[0]
            self.assertGreater(deadline, time.time() + 0.7)
            self.assertFalse(any(path == "/later" for path, _, _ in seen))
            time.sleep(max(0, deadline - time.time()) + 0.03)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"downloaded": 2})
            statuses = [row[0] for row in store.db.execute("SELECT status FROM fetches ORDER BY rowid")]
            self.assertEqual(statuses, ["rate_limited", "downloaded", "downloaded"])

    def test_robots_disallow_and_scope_boundary(self):
        routes = {
            "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /rules/denied\n"),
            "/rules": html('<title>Index</title><a href="/rules/denied">Denied</a><a href="/rules/ok">Allowed</a><a href="/rules-evil">Outside</a><a href="/rules/%2e%2e/private">Escape</a>'),
            "/rules/ok": html("Allowed text"),
        }
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/rules"], allow=[{"host": base.split("://")[1], "path_prefixes": ["/rules"]}])
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"downloaded": 2, "robots_disallowed": 1})
            paths = [path for path, _, _ in seen]
            self.assertNotIn("/rules/denied", paths)
            self.assertNotIn("/rules-evil", paths)
            self.assertNotIn("/rules/%2e%2e/private", paths)
            self.assertEqual(paths.count("/robots.txt"), 1)
            self.assertEqual(len(list((self.root / "controls" / "robots").glob("*.json"))), 1)

    def test_partial_large_response_is_not_reported_as_downloaded(self):
        with fixture_server({"/big": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\n" + b"X" * 1000)}) as (base, _):
            store, artifacts = self.create(base, ["/big"], max_response_bytes=80, respect_robots=False)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"too_large_or_incomplete": 1})
            row = store.db.execute("SELECT * FROM resources").fetchone()
            self.assertEqual(row["raw_complete"], 0)
            self.assertEqual((self.root / row["raw_path"]).stat().st_size, 80)
            self.assertTrue(row["raw_path"].startswith("raw_partial/"))

    def test_interrupted_claim_recovery_and_idempotent_ingestion(self):
        with fixture_server({"/page": html("Recovered interrupted queue")}) as (base, seen):
            store, artifacts = self.create(base, ["/page"])
            resource = store.claim()
            self.assertIsNotNone(resource)
            store.ingest(self.root / "seeds.jsonl")
            self.assertEqual(store.db.execute("SELECT count(*) FROM resources").fetchone()[0], 1)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"downloaded": 1})
            self.assertEqual(sum(path == "/page" for path, _, _ in seen), 1)
            event = store.db.execute("SELECT details_json FROM events WHERE kind='recovered_interrupted_frontier'").fetchone()
            self.assertEqual(json.loads(event[0])["count"], 1)

    def test_seed_scope_and_additional_context_expand_existing_graph(self):
        routes = {"/rules": html('<a href="/rules/deeper">Deeper</a>'), "/rules/deeper": html("Deeper text")}
        with fixture_server(routes) as (base, seen):
            seed = {"url": base + "/rules", "scope": {"host": base.split("://")[1], "path_prefixes": ["/rules"]}}
            store, artifacts = self.create(base, [seed], max_depth=0)
            self.crawl(store, artifacts)
            self.assertEqual(store.summary()["resource_count"], 1)
            store.cfg.max_depth = 2
            second = self.root / "more.jsonl"
            second.write_text(json.dumps({"url": base + "/rules", "category": "additional-context"}), encoding="utf-8")
            store.ingest(second)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["downloaded_resources"], 2)
            self.assertEqual(sum(path == "/rules" for path, _, _ in seen), 1)

    def test_per_host_delay_applies_with_parallel_workers(self):
        routes = {f"/{n}": html(f"Page {n}") for n in range(4)}
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, list(routes), respect_robots=False, workers=3, per_host_delay=0.07)
            self.crawl(store, artifacts)
            starts = sorted(timestamp for _, timestamp, _ in seen)
            self.assertEqual(len(starts), 4)
            self.assertTrue(all(right - left >= 0.045 for left, right in zip(starts, starts[1:])), starts)

    def test_redirect_cycles_and_external_redirects_remain_unresolved(self):
        routes = {
            "/one": (301, {"Location": "/two"}, b""),
            "/two": (301, {"Location": "/one"}, b""),
            "/outside": (302, {"Location": "https://external.invalid/document"}, b""),
        }
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/one", "/outside"])
            result = self.crawl(store, artifacts)
            self.assertTrue(result["frontier_exhausted"])
            self.assertFalse(result["all_enqueued_resources_downloaded"])
            self.assertEqual(result["unresolved_redirects"], 3)
            self.assertEqual(result["statuses"], {"redirect": 3})
            self.assertEqual(sum(path == "/one" for path, _, _ in seen), 1)

    def test_transient_robots_failure_is_refreshed_after_backoff(self):
        routes = {
            "/robots.txt": lambda n: (503, {"Retry-After": "0", "Content-Type": "text/plain"}, b"Unavailable") if n == 1 else (200, {"Content-Type": "text/plain"}, b"User-agent: *\nAllow: /\n"),
            "/page": html("Available after robots recovered"),
        }
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/page"])
            result = self.crawl(store, artifacts, wait_retries=True)
            self.assertEqual(result["statuses"], {"downloaded": 1})
            self.assertEqual(sum(path == "/robots.txt" for path, _, _ in seen), 2)
            self.assertEqual([row[0] for row in store.db.execute("SELECT status FROM fetches ORDER BY rowid")], ["robots_unavailable", "downloaded"])

    def test_slow_robots_host_yields_workers_and_preserves_full_delay(self):
        original_root = self.root
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.root = original_root / str(workers)
                slow_routes = {
                    "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nCrawl-delay: 1\nAllow: /\n"),
                    "/one": html("Slow host one"), "/two": html("Slow host two"), "/three": html("Slow host three"),
                }
                with fixture_server(slow_routes) as (slow, slow_seen), fixture_server({"/fast": html("Unrelated host")}) as (fast, fast_seen):
                    seeds = [{"url": slow + path} for path in ("/one", "/two", "/three")] + [{"url": fast + "/fast"}]
                    store, artifacts = self.create(slow, seeds, workers=workers, max_retries=0, allow=[{"host": url.split("://")[1], "path_prefixes": ["/"]} for url in (slow, fast)])
                    result = self.crawl(store, artifacts, pages=4)
                    self.assertEqual(result["statuses"], {"downloaded": 4})
                    self.assertEqual(result["run_processed"], 4)
                    self.assertEqual(result["fetch_attempts"], 4)
                    self.assertTrue(all(row["attempts"] == 1 and row["retry_count"] == 0 for row in store.db.execute("SELECT attempts,retry_count FROM resources")))
                    fast_at = next(at for path, at, _ in fast_seen if path == "/fast")
                    slow_pages = [at for path, at, _ in slow_seen if path != "/robots.txt"]
                    self.assertLess(fast_at, min(slow_pages))
                    starts = [at for _, at, _ in slow_seen]
                    self.assertEqual(len(starts), 4)
                    self.assertTrue(all(b - a >= 0.97 for a, b in zip(starts, starts[1:])), starts)
        self.root = original_root

    def test_one_active_request_per_host_and_round_robin_with_short_delay(self):
        ongoing, maximum = 0, 0
        lock = threading.Lock()

        def respond(_):
            nonlocal ongoing, maximum
            with lock:
                ongoing += 1
                maximum = max(maximum, ongoing)
            time.sleep(0.06)
            with lock:
                ongoing -= 1
            return html("Sequential host response")

        original_root = self.root
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.root = original_root / str(workers)
                with fixture_server({"/a": respond, "/b": respond, "/c": respond}) as (slow, slow_seen), fixture_server({"/fast": html("Other host")}) as (fast, fast_seen):
                    seeds = [{"url": slow + path} for path in ("/a", "/b", "/c")] + [{"url": fast + "/fast"}]
                    store, artifacts = self.create(slow, seeds, workers=workers, respect_robots=False, per_host_delay=0.001, allow=[{"host": url.split("://")[1], "path_prefixes": ["/"]} for url in (slow, fast)])
                    result = self.crawl(store, artifacts)
                    self.assertEqual(result["statuses"], {"downloaded": 4})
                    self.assertEqual(maximum, 1)
                    self.assertLess(fast_seen[0][1], slow_seen[1][1])
        self.root = original_root

    def test_retry_after_host_does_not_stall_unrelated_host(self):
        slow_routes = {"/limited": (429, {"Retry-After": "120", "Content-Type": "text/plain"}, b"Wait"), "/later": html("Do not request during cooldown")}
        with fixture_server(slow_routes) as (slow, slow_seen), fixture_server({"/fast": html("Other host")}) as (fast, fast_seen):
            seeds = [{"url": slow + path} for path in ("/limited", "/later")] + [{"url": fast + "/fast"}]
            store, artifacts = self.create(slow, seeds, respect_robots=False, workers=1, allow=[{"host": url.split("://")[1], "path_prefixes": ["/"]} for url in (slow, fast)])
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"downloaded": 1, "pending": 1, "retry_wait": 1})
            self.assertEqual(result["fetch_attempts"], 2)
            self.assertEqual([path for path, _, _ in slow_seen], ["/limited"])
            self.assertEqual([path for path, _, _ in fast_seen], ["/fast"])
            deadline = store.db.execute("SELECT cooldown_until FROM hosts WHERE host=?", (slow.split("://")[1],)).fetchone()[0]
            self.assertGreater(deadline, time.time() + 119)

    def test_robots_redirect_progress_survives_pacing_deferral(self):
        routes = {
            "/robots.txt": (302, {"Location": "/robots-final.txt"}, b"Moved robots"),
            "/robots-final.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nAllow: /\n"),
            "/page": html("Page after redirected robots"),
        }
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/page"], per_host_delay=0.08, max_retries=0)
            result = self.crawl(store, artifacts, pages=1)
            self.assertEqual(result["statuses"], {"downloaded": 1})
            self.assertEqual(result["fetch_attempts"], 1)
            self.assertEqual([path for path, _, _ in seen], ["/robots.txt", "/robots-final.txt", "/page"])
            control = json.loads(next((self.root / "controls" / "robots").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(control["observations"]), 2)
            self.assertNotIn("in_progress", control)

    def test_pacing_checkpoint_restores_deadline_and_legacy_robots_delay(self):
        with fixture_server({"/one": html("One"), "/two": html("Two")}) as (base, seen):
            store, artifacts = self.create(base, ["/one", "/two"], per_host_delay=0.35, respect_robots=False)
            self.crawl(store, artifacts, pages=1)
            checkpoint = json.loads((self.root / "controls" / "host_pacing.json").read_text(encoding="utf-8"))
            host = base.split("://")[1]
            saved_deadline = checkpoint["hosts"][host]["next_start_at"]
            restored = Gate(store.cfg, {}, threading.Event(), artifacts)
            self.assertGreaterEqual(restored.ready_at(host), saved_deadline - 0.01)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["statuses"], {"downloaded": 2})
            self.assertGreaterEqual(seen[1][1] - seen[0][1], 0.33)

        legacy = Artifacts(self.root / "legacy", store.cfg)
        raw_path = legacy.write("raw/legacy-robots.txt", b"User-agent: *\nCrawl-delay: 120\n")
        legacy.json("controls/robots/legacy.json", {"origin": "https://slow.example.gov", "observations": [{"http_status": 200, "raw_complete": True, "raw_path": raw_path, "headers": {"content-type": "text/plain"}}]})
        restored = Gate(store.cfg, {}, threading.Event(), legacy)
        self.assertGreaterEqual(restored.ready_at("slow.example.gov"), time.time() + 119.9)

    def test_scope_expansion_replays_saved_links_without_refetching_parent(self):
        routes = {"/index": html('<a href="/child">Child</a>'), "/child": html("Child document")}
        with fixture_server(routes) as (base, seen):
            store, artifacts = self.create(base, ["/index"], max_depth=0)
            self.crawl(store, artifacts)
            self.assertEqual(store.summary()["resource_count"], 1)
            store.cfg.max_depth = 1
            result = self.crawl(store, artifacts)
            self.assertEqual(result["downloaded_resources"], 2)
            self.assertEqual(sum(path == "/index" for path, _, _ in seen), 1)
            self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE kind='scope_reconciled'").fetchone()[0], 1)

    def test_disk_guard_leaves_request_pending(self):
        with fixture_server({"/page": html("Must not be fetched")}) as (base, seen):
            store, artifacts = self.create(base, ["/page"], min_free_bytes=10**18)
            result = self.crawl(store, artifacts)
            self.assertEqual(result["stop_reason"], "disk_guard")
            self.assertEqual(result["pending_resources"], 1)
            self.assertEqual(len(seen), 0)
            self.assertEqual(store.db.execute("SELECT status FROM fetches").fetchone()[0], "disk_guard")

    def test_url_validation_and_action_filters(self):
        self.assertEqual(canonical_url("HTTPS://EXAMPLE.ORG:443/a?b=2&a=1#top"), "https://example.org/a?b=2&a=1")
        self.assertIsNone(canonical_url("https://user:secret@example.org/"))
        self.assertIsNone(canonical_url("javascript:alert(1)"))
        self.assertIsNone(canonical_url("https://example.org/a\nInjected"))
        cfg = Config.from_dict({"allow": [{"host": "example.org", "path_prefixes": ["/rules"]}]})
        self.assertFalse(cfg.allowed("https://example.org/rules?action=delete")[0])
        self.assertFalse(cfg.allowed("https://example.org/rules/%2e%2e/admin")[0])
        self.assertTrue(cfg.allowed("https://example.org/rules/1?sort=asc")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
