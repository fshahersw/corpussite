"""Two-process coordination tests. All HTTP is served by loopback fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
from corpus_crawler import Config, PacingDeferred, SharedHosts, Store, import_shared_collections
from test_corpus_crawler import fixture_server, html


class SharedHostTests(unittest.TestCase):
    def setUp(self):
        scratch = PIPELINE / "tests" / "_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="shared-fixture-", dir=scratch)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(scratch.resolve()))
        self.shared = self.root / "shared"
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=10)
        self.temp.cleanup()

    def collection(self, name, urls, **overrides):
        root = self.root / name
        cfg = Config.from_dict({"allow": [{"host": host, "path_prefixes": ["/"]} for host in sorted({url.split("://")[1].split("/")[0] for url in urls})],
            "workers": 1, "per_host_delay": 0.05, "respect_robots": False, "min_free_bytes": 0,
            "max_retries": 0, "shared_host_dir": str(self.shared), **overrides})
        store = Store(root, cfg)
        try:
            import dataclasses
            (root / "config.json").write_text(json.dumps(dataclasses.asdict(cfg)), encoding="utf-8")
            seeds = root / "seeds.jsonl"
            seeds.write_text("\n".join(json.dumps({"url": url, "source_family": "local_fixture"}) for url in urls), encoding="utf-8")
            store.ingest(seeds)
        finally:
            store.close()
        return root

    def start(self, root, pages=0):
        child = subprocess.Popen([sys.executable, str(PIPELINE / "corpus_crawler.py"), "--root", str(root), "run", "--max-pages", str(pages), "--max-seconds", "12"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def finish(self, child):
        out, err = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, out + err)
        return out

    def rows(self, root):
        db = sqlite3.connect((root / "corpus.sqlite3").as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute("SELECT * FROM resources ORDER BY id")]
        finally:
            db.close()

    def test_two_processes_never_overlap_host_and_busy_host_releases_worker(self):
        started, release, fast_done = threading.Event(), threading.Event(), threading.Event()

        def slow(_):
            started.set()
            release.wait(8)
            return html("Long response")

        def fast(_):
            fast_done.set()
            return html("Independent host progresses")

        with fixture_server({"/slow": slow, "/second": html("Second host request")}) as (same, seen), fixture_server({"/fast": fast}) as (other, _):
            first_root = self.collection("first", [same + "/slow"], per_host_delay=0.2)
            second_root = self.collection("second", [same + "/second", other + "/fast"], per_host_delay=0.01)
            first = self.start(first_root)
            try:
                self.assertTrue(started.wait(4))
                second = self.start(second_root)
                self.assertTrue(fast_done.wait(4), "The shared busy host occupied the second process's only worker")
                time.sleep(0.35)  # Longer than the host delay; a live OS owner must remain exclusive.
                self.assertEqual([path for path, _, _ in seen], ["/slow"])
            finally:
                release.set()
            self.finish(first)
            self.finish(second)
            self.assertEqual([path for path, _, _ in seen], ["/slow", "/second"])
            self.assertEqual([row["status"] for row in self.rows(second_root)], ["downloaded", "downloaded"])
            self.assertTrue(all(row["attempts"] == 1 for row in self.rows(second_root)))

    def test_observed_robots_delay_is_shared_with_a_second_process(self):
        routes = {"/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nCrawl-delay: 1\nAllow: /\n"), "/one": html("One"), "/two": html("Two")}
        with fixture_server(routes) as (base, seen):
            first = self.collection("robots-observer", [base + "/one"], respect_robots=True)
            second = self.collection("other-collection", [base + "/two"])
            self.finish(self.start(first))
            self.finish(self.start(second))
            self.assertEqual([path for path, _, _ in seen], ["/robots.txt", "/one", "/two"])
            self.assertTrue(all(b[1] - a[1] >= 0.97 for a, b in zip(seen, seen[1:])), seen)
            policy, busy = SharedHosts(self.shared, second).inspect(base.split("://")[1])
            self.assertEqual(policy["delay_seconds"], 1)
            self.assertFalse(busy)

    def test_access_blocks_and_retry_after_propagate_without_false_attempts(self):
        for label, response, expected in (("forbidden", html("Forbidden", 403), "forbidden"), ("cooldown", (429, {"Retry-After": "120", "Content-Type": "text/plain"}, b"Wait"), "rate_limited"), ("challenge", html("<title>Just a moment...</title>Checking your browser"), "challenge")):
            with self.subTest(label=label), fixture_server({"/restricted": response, "/later": html("Must remain pending")}) as (limited, seen), fixture_server({"/fast": html("Available")}) as (fast, _):
                first = self.collection(label + "-observer", [limited + "/restricted"])
                second = self.collection(label + "-other", [limited + "/later", fast + "/fast"])
                self.finish(self.start(first))
                self.finish(self.start(second))
                self.assertEqual(self.rows(first)[0]["status"], expected)
                self.assertEqual([path for path, _, _ in seen], ["/restricted"])
                rows = self.rows(second)
                self.assertEqual([row["status"] for row in rows], ["pending", "downloaded"])
                self.assertEqual([row["attempts"] for row in rows], [0, 1])
                policy, busy = SharedHosts(self.shared, second).inspect(limited.split("://")[1])
                self.assertFalse(busy)
                if label == "cooldown":
                    self.assertGreater(policy["cooldown_until"], time.time() + 118)
                else:
                    self.assertEqual(policy["pause_reason"], expected)

    def test_crashed_owner_recovery_preserves_pacing_cooldown_and_blocks(self):
        program = "import os,sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from corpus_crawler import SharedHosts;s=SharedHosts(Path(sys.argv[2]),Path(sys.argv[3]));s.reserve(sys.argv[4],0.4);s.merge(sys.argv[4],cooldown=time.time()+0.7,reason=sys.argv[5] or None);os._exit(0)"
        for host, reason in (("stale.test", ""), ("blocked.test", "forbidden")):
            child = subprocess.Popen([sys.executable, "-c", program, str(PIPELINE), str(self.shared), str(self.root / "crashed"), host, reason], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.children.append(child)
            self.finish(child)
            coordinator = SharedHosts(self.shared, self.root / "replacement")
            before, busy = coordinator.inspect(host)
            self.assertFalse(busy)
            self.assertIsNotNone(before["owner_token"])
            with self.assertRaises(PacingDeferred):
                coordinator.reserve(host, 0.01)
            recovered, _ = coordinator.inspect(host)
            self.assertIsNone(recovered["owner_token"])
            self.assertGreaterEqual(recovered["next_start_at"], before["next_start_at"])
            self.assertGreaterEqual(recovered["cooldown_until"], before["cooldown_until"])
            self.assertGreater(recovered["next_start_at"], time.time() + 0.35)
            time.sleep(max(recovered["next_start_at"], recovered["cooldown_until"]) - time.time() + 0.04)
            if reason:
                with self.assertRaises(PacingDeferred):
                    coordinator.reserve(host, 0.01)
                self.assertEqual(coordinator.inspect(host)[0]["pause_reason"], "forbidden")
            else:
                coordinator.reserve(host, 0.01)
                coordinator.release(host)

    def test_offline_import_preserves_collection_databases_and_unions_policy(self):
        root = self.collection("legacy", ["https://offline.test/page"])
        db_path = root / "corpus.sqlite3"
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO hosts VALUES(?,?,?,?)", ("offline.test", "forbidden", time.time() + 180, "fixture"))
        db.commit()
        before = db.execute("SELECT * FROM hosts").fetchall()
        db.close()
        controls = root / "controls"
        controls.mkdir()
        started = time.time()
        (controls / "host_pacing.json").write_text(json.dumps({"hosts": {"offline.test": {"last_start_at": started, "next_start_at": started + 120, "delay_seconds": 120}}}), encoding="utf-8")
        result = import_shared_collections(self.shared, [root])
        policy, _ = SharedHosts(self.shared, root).inspect("offline.test")
        self.assertEqual(policy["delay_seconds"], 120)
        self.assertEqual(policy["pause_reason"], "forbidden")
        self.assertGreaterEqual(policy["next_start_at"], started + 120)
        self.assertEqual(result["collections"][0]["hosts_imported"], 1)
        db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(db.execute("SELECT * FROM hosts").fetchall(), before)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
