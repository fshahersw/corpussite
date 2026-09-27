"""Offline provider fixtures; no credentials, network, or production queue use."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
from firecrawl_batch_worker import API, BatchWorker, SelectionError, read_selection, status_path, worker_lock

JOB = "01a09a20-3a42-7564-bc70-ec74b3e68576"
RULES = ["https://trellis.law/state-rules/arizona/rule-1", "https://trellis.law/state-rules/illinois/rule-2"]
COUNTIES = ["https://trellis.law/coverage/arizona/pima", "https://trellis.law/coverage/arizona/pinal", "https://trellis.law/coverage/illinois/cook"]


def page(url, status=200, **extra):
    return {"markdown": "Official procedural rule", "html": "<main>Rule</main>", "links": [], "metadata": {"sourceURL": url, "statusCode": status, "creditsUsed": 1, "proxyUsed": "basic", **extra}}


def poll(data, status="completed", **extra):
    return {"success": True, "status": status, "completed": len(data), "total": len(data), "creditsUsed": len(data), "data": data, **extra}


class Fake:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if not self.responses:
            raise AssertionError("Unexpected provider call: " + method + " " + path)
        expected_method, expected_path, result = self.responses.pop(0)
        assert (method, path) == (expected_method, expected_path), self.calls
        if isinstance(result, Exception):
            raise result
        return result


class BatchTests(unittest.TestCase):
    def setUp(self):
        scratch = PIPELINE / "tests" / "_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="batch-fixture-", dir=scratch)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(scratch.resolve()))
        self.base, self.state = self.root / "trellis", self.root / "trellis" / "worker"
        self.workers = []
        self.at = 1000.0

    def tearDown(self):
        for worker in self.workers:
            try:
                worker.q.db.close()
            except Exception:
                pass
        self.temp.cleanup()

    def worker(self, fake, **kwargs):
        worker = BatchWorker(fake, self.base, self.state, min_free=0, clock=lambda: self.at, **kwargs)
        self.workers.append(worker)
        return worker

    def ready(self, responses=(), urls=RULES, **kwargs):
        fake = Fake([("GET", "team/credit-usage", (200, {"data": {"remainingCredits": 100}})), *responses])
        worker = self.worker(fake, **kwargs)
        for url in urls:
            worker.q.add(url)
        worker.q.db.commit()
        self.assertTrue(worker.preflight())
        self.assertTrue(worker.prepare())
        return worker, fake

    def accepted(self, responses=(), **kwargs):
        worker, fake = self.ready([("POST", "batch/scrape", (200, {"success": True, "id": JOB, "url": API + "batch/scrape/" + JOB})), *responses], **kwargs)
        self.assertTrue(worker.submit())
        return worker, fake

    def selection_file(self, values, jsonl=False):
        path = self.root / ("selected.jsonl" if jsonl else "selected.json")
        path.write_text("\n".join(json.dumps(v) for v in values) if jsonl else json.dumps(values), encoding="utf-8")
        return path

    def test_selection_preserves_global_order_same_category_runs_and_no_expansion(self):
        selected = [*COUNTIES[:2], RULES[1], COUNTIES[2], RULES[0]]
        path = self.selection_file(selected)
        extra = "https://trellis.law/state-rules/florida/not-selected"
        judge = "https://trellis.law/judge/jane.doe"
        groups = [COUNTIES[:2], [RULES[1]], [COUNTIES[2]], [RULES[0]]]
        responses = []
        for group in groups:
            data = [page(url) for url in group]
            data[0]["links"] = [extra, judge]
            responses.extend([("POST", "batch/scrape", (200, {"success": True, "id": JOB})),
                              ("GET", "batch/scrape/" + JOB, (200, poll(data)))])
        worker, fake = self.ready(responses, urls=[*RULES, *COUNTIES, judge], selection_file=path)
        identity = worker.selection["sha256"]
        for index, group in enumerate(groups):
            if index:
                self.assertTrue(worker.prepare())
            self.assertEqual([r["url"] for r in worker.job["rows"]], group)
            self.assertEqual(len({r["category"] for r in worker.job["rows"]}), 1)
            self.assertEqual(worker.job["selection"]["sha256"], identity)
            self.assertTrue(worker.submit())
            self.assertEqual(fake.calls[-1][2]["urls"], group)
            self.assertTrue(worker.poll())
            self.at += 31
        self.assertFalse(worker.prepare())
        self.assertEqual(worker.reason, "selection_exhausted_or_not_pending")
        self.assertFalse(worker.active_file.exists())
        self.assertEqual(worker.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], len(selected))
        for url in (extra, judge):
            self.assertEqual(tuple(worker.q.db.execute("SELECT status,attempts FROM frontier WHERE url=?", (url,)).fetchone()), ("pending", 0))

    def test_selection_skips_saved_captures_and_missing_urls_without_api_calls(self):
        missing = "https://trellis.law/coverage/arizona/never-enqueued"
        path = self.selection_file([COUNTIES[0], {"url": RULES[0]}, missing], jsonl=True)
        fake = Fake([])
        worker = self.worker(fake, selection_file=path)
        for url in (COUNTIES[0], RULES[0], RULES[1], "https://trellis.law/judges/arizona"):
            worker.q.add(url)
        worker.q.db.execute("UPDATE frontier SET status='captured_elsewhere' WHERE url=?", (RULES[0],))
        worker.q.db.commit()
        saved = self.base / "counties" / "arizona_pima.firecrawl.json"
        saved.parent.mkdir(parents=True)
        saved.write_text(json.dumps(page(COUNTIES[0])), encoding="utf-8")
        result = worker.run(once=True)
        self.assertEqual(result["status"], "selection_exhausted_or_not_pending")
        self.assertEqual(result["selected_status_counts"], {"downloaded": 1, "captured_elsewhere": 1, "not_in_frontier": 1})
        self.assertEqual(result["selected_eligible_pending"], 0)
        self.assertEqual(fake.calls, [])
        self.assertFalse(worker.active_file.exists())

    def test_selected_prepared_batch_resumes_same_rows_options_and_identity(self):
        path = self.selection_file([COUNTIES[0], *RULES])
        worker, _ = self.ready(urls=[*RULES, COUNTIES[0]], selection_file=path, cache_max_age_ms=86400000)
        old_rows = worker.job["rows"].copy()
        old_identity = worker.job["local_id"]
        worker.q.db.close()
        # Formatting and the equivalent record form may change; URL order may not.
        path.write_text(json.dumps({"urls": [{"url": url} for url in [COUNTIES[0], *RULES]]}, indent=2), encoding="utf-8")
        fake = Fake([("GET", "team/credit-usage", (200, {"data": {"remainingCredits": 100}})),
                     ("POST", "batch/scrape", (200, {"success": True, "id": JOB}))])
        resumed = self.worker(fake, selection_file=path)
        resumed.load()
        self.assertEqual(resumed.job["local_id"], old_identity)
        self.assertEqual(resumed.job["rows"], old_rows)
        self.assertTrue(resumed.preflight())
        self.assertTrue(resumed.submit())
        self.assertEqual(fake.calls[-1][2]["urls"], [COUNTIES[0]])
        self.assertEqual(fake.calls[-1][2]["maxAge"], 86400000)

    def test_selected_active_batch_rejects_missing_or_reordered_selector_before_calls(self):
        path = self.selection_file([COUNTIES[0], RULES[0]])
        worker, _ = self.ready(urls=[RULES[0], COUNTIES[0]], selection_file=path)
        worker.q.db.close()
        missing_fake = Fake([])
        resumed = self.worker(missing_fake)
        self.assertEqual(resumed.run(once=True)["status"], "paused_selection_mismatch")
        self.assertEqual(missing_fake.calls, [])
        path.write_text(json.dumps([RULES[0], COUNTIES[0]]), encoding="utf-8")
        changed_fake = Fake([])
        changed = self.worker(changed_fake, selection_file=path)
        self.assertEqual(changed.run(once=True)["status"], "paused_selection_mismatch")
        self.assertEqual(changed_fake.calls, [])

    def test_capture_after_preparation_is_preserved_and_not_resubmitted(self):
        path = self.selection_file([COUNTIES[0]])
        worker, _ = self.ready(urls=[COUNTIES[0]], selection_file=path)
        worker.q.db.execute("UPDATE frontier SET status='captured_elsewhere' WHERE url=?", (COUNTIES[0],))
        worker.q.db.commit()
        worker.q.db.close()
        fake = Fake([])
        resumed = self.worker(fake, selection_file=path)
        resumed.load()
        resumed.credits = 100
        self.assertFalse(resumed.submit())
        self.assertEqual(resumed.reason, "paused_prepared_capture_or_access_conflict")
        self.assertEqual(resumed.q.db.execute("SELECT status FROM frontier WHERE url=?", (COUNTIES[0],)).fetchone()[0], "captured_elsewhere")
        self.assertEqual(fake.calls, [])

    def test_selection_deduplicates_first_slot_and_rejects_scalar_input(self):
        path = self.selection_file([COUNTIES[0], {"url": RULES[0]}, COUNTIES[0]], jsonl=True)
        self.assertEqual(read_selection(path)["urls"], [COUNTIES[0], RULES[0]])
        path = self.selection_file("https://trellis.law/judges/arizona")
        with self.assertRaises(SelectionError):
            self.worker(Fake([]), selection_file=path)

    def test_selected_judge_profile_runs_ahead_of_unselected_laws_and_directories(self):
        judge = 'https://trellis.law/judge/jane.doe'
        directory = 'https://trellis.law/judges/arizona?page=2'
        path = self.selection_file([judge])
        worker, fake = self.accepted(urls=[*RULES, directory, judge], selection_file=path)
        self.assertEqual(fake.calls[1][2]['urls'], [judge])
        self.assertEqual(worker.job['phase_category'], 'judge_profile')
        worker.check_job_selection()
        for url in [*RULES, directory]:
            self.assertEqual(tuple(worker.q.db.execute('SELECT status,attempts FROM frontier WHERE url=?', (url,)).fetchone()), ('pending', 0))

    def test_judge_selector_rejects_case_document_and_cross_host_urls(self):
        for url in ['https://trellis.law/case/123/example', 'https://trellis.law/doc/123/example',
                    'https://example.com/judge/jane.doe', 'https://trellis.law/judge/jane.doe?output=pdf']:
            with self.subTest(url=url):
                with self.assertRaises(SelectionError):
                    read_selection(self.selection_file([url]))

    def test_exact_phases_credit_reserve_and_31_second_post_gap(self):
        later = ["https://trellis.law/judges/arizona", "https://trellis.law/judge/jane.doe", "https://trellis.law/coverage/arizona/pima", "https://trellis.law/case/123/unrelated"]
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([page(RULES[0])]))), ("POST", "batch/scrape", (200, {"success": True, "id": JOB}))], urls=[*RULES, *later], batch_size=1)
        first_payload = fake.calls[1][2]
        self.assertEqual(first_payload["urls"], [RULES[0]])
        self.assertEqual(first_payload["maxConcurrency"], 2)
        self.assertEqual(first_payload["proxy"], "basic")
        self.assertFalse(first_payload["skipTlsVerification"])
        self.assertTrue(worker.poll())
        self.assertTrue(worker.prepare())
        self.assertEqual(worker.job["phase_priority"], 10)
        self.assertFalse(worker.submit())
        self.assertEqual(len(fake.calls), 3)
        self.at += 31
        self.assertTrue(worker.submit())
        self.assertEqual(fake.calls[-1][2]["urls"], [RULES[1]])
        worker.credits = 1
        worker.job["state"] = "prepared"
        self.at += 31
        self.assertFalse(worker.submit())
        self.assertEqual(worker.reason, "paused_credit_reserve")
        self.assertFalse(worker.q.db.execute("SELECT 1 FROM frontier WHERE url LIKE '%/case/%'").fetchone())

    def test_larger_cached_batch_shrinks_to_available_credits(self):
        fake = Fake([("GET", "team/credit-usage", (200, {"data": {"remainingCredits": 27}})),
                     ("POST", "batch/scrape", (200, {"success": True, "id": JOB}))])
        worker = self.worker(fake, batch_size=50, cache_max_age_ms=86400000, page_timeout_ms=180000)
        for n in range(60):
            worker.q.add("https://trellis.law/state-rules/az/rule-" + str(n))
        worker.q.add("https://trellis.law/judges/arizona")
        worker.q.db.commit()
        self.assertTrue(worker.preflight())
        self.assertTrue(worker.prepare())
        self.assertEqual(len(worker.job["rows"]), 26)
        self.assertTrue(all("/state-rules/" in row["url"] for row in worker.job["rows"]))
        self.assertTrue(worker.submit())
        payload = fake.calls[-1][2]
        self.assertEqual(payload["maxAge"], 86400000)
        self.assertEqual(payload["timeout"], 180000)
        self.assertEqual(payload["maxConcurrency"], 2)
        self.assertFalse(payload["skipTlsVerification"])

    def test_prepared_cache_policy_survives_changed_restart_defaults(self):
        worker, _ = self.ready(batch_size=1, cache_max_age_ms=86400000, page_timeout_ms=180000)
        worker.q.db.close()
        fake = Fake([("GET", "team/credit-usage", (200, {"data": {"remainingCredits": 100}})),
                     ("POST", "batch/scrape", (200, {"success": True, "id": JOB}))])
        resumed = self.worker(fake)
        resumed.load()
        self.assertTrue(resumed.preflight())
        self.assertTrue(resumed.submit())
        self.assertEqual(fake.calls[-1][2]["maxAge"], 86400000)
        self.assertEqual(fake.calls[-1][2]["timeout"], 180000)

    def test_uncertain_post_is_not_resubmitted_after_restart(self):
        worker, _ = self.ready([("POST", "batch/scrape", TimeoutError("Uncertain transport"))])
        self.assertFalse(worker.submit())
        self.assertEqual(worker.job["state"], "uncertain_submission")
        manifest = json.loads(worker.job_path().read_text())
        self.assertEqual(len(manifest["rows"]), 2)
        worker.q.db.close()
        restarted_fake = Fake([])
        restarted = self.worker(restarted_fake)
        result = restarted.run(once=True)
        self.assertEqual(result["status"], "paused_uncertain_submission")
        self.assertEqual(restarted_fake.calls, [])
        self.assertTrue(restarted.active_file.exists())

    def test_returned_job_id_recovers_from_raw_submission_without_another_post(self):
        worker, _ = self.ready()
        worker.job.update(state="submitting", post_sequence=1)
        worker.save_job()
        worker.raw("submission_1", 200, {"success": True, "id": JOB, "url": API + "batch/scrape/" + JOB})
        worker.q.db.close()
        fake = Fake([])
        restarted = self.worker(fake)
        restarted.load()
        self.assertEqual(restarted.job["job_id"], JOB)
        self.assertEqual(restarted.job["state"], "submitted")
        self.assertEqual(fake.calls, [])

    def test_unicode_submission_and_poll_replay_use_utf8_under_windows_locale(self):
        text = "Court’s “quoted rule” — café § 12"
        worker, _ = self.ready()
        worker.job.update(state="submitting", post_sequence=1, note=text, poll_files=["poll_0.json"])
        worker.save_job()
        worker.raw("submission_1", 200, {"success": True, "id": JOB, "note": text})
        data = page(RULES[0], title=text)
        data["markdown"] = text
        rawfile = worker.raw("poll_0", 200, poll([data], "scraping"))
        # U+201D produces UTF-8 byte 0x9d, which fails strict cp1252 decoding.
        with self.assertRaises(UnicodeDecodeError):
            rawfile.read_bytes().decode("cp1252")
        for path, value in ((worker.rate_file, {"next_post_at": self.at, "note": text}),
            (self.state / "status.json", {"cooldown_until_epoch": 0, "note": text}),
            (worker.active_file, {"local_id": worker.job["local_id"], "note": text})):
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        worker.q.db.close()
        original_read = Path.read_text
        reads = []

        def windows_read(path, encoding=None, errors=None):
            if path.is_relative_to(self.root):
                reads.append((path, encoding))
            return original_read(path, encoding=encoding or "cp1252", errors=errors)

        fake = Fake([])
        with mock.patch.object(Path, "read_text", windows_read):
            restarted = self.worker(fake)
            restarted.load()
        self.assertGreaterEqual(len(reads), 6)
        self.assertTrue(all(encoding == "utf-8" for _, encoding in reads), reads)
        self.assertEqual(restarted.job["job_id"], JOB)
        self.assertEqual(fake.calls, [])
        self.assertEqual(restarted.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        output = next((self.base / "rules").glob("*.firecrawl.json"))
        replayed = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(replayed["markdown"], text)
        self.assertEqual(replayed["metadata"]["title"], text)

    def test_repeated_paginated_results_and_resume_record_each_attempt_and_credit_once(self):
        next_url = API + "batch/scrape/" + JOB + "?skip=1"
        worker, _ = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([page(RULES[0])], "scraping", creditsUsed=2, next=next_url))),
            ("GET", "batch/scrape/" + JOB + "?skip=1", (200, poll([page(RULES[1])], "scraping", creditsUsed=2)))])
        self.assertFalse(worker.poll())
        self.assertEqual(worker.run_used, 2)
        self.assertEqual(worker.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 2)
        worker.q.db.close()
        fake = Fake([("GET", "batch/scrape/" + JOB, (200, poll([page(url) for url in RULES])))])
        restarted = self.worker(fake)
        restarted.load()
        self.assertEqual(restarted.run_used, 0)
        self.assertTrue(restarted.poll())
        self.assertEqual(restarted.run_used, 0)
        self.assertEqual(restarted.q.db.execute("SELECT count(*),sum(credits_used) FROM attempts").fetchone()[:], (2, 2))
        self.assertEqual(len(list((self.base / "rules").glob("*.firecrawl.json"))), 2)
        self.assertFalse(restarted.active_file.exists())
        self.assertFalse(any(call[0] == "POST" for call in fake.calls))

    def test_target_barrier_cancels_running_job_and_keeps_hold(self):
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([page(RULES[0], 403)], "scraping"))),
            ("DELETE", "batch/scrape/" + JOB, (200, {"success": True, "status": "cancelled"}))])
        self.assertFalse(worker.poll())
        self.assertEqual(worker.reason, "paused_target_access")
        self.assertEqual(worker.q.db.execute("SELECT status FROM frontier WHERE url=?", (RULES[0],)).fetchone()[0], "access_blocked")
        self.assertTrue(worker.job["cancel_acknowledged"])
        self.assertTrue(worker.active_file.exists())
        self.assertEqual(fake.calls[-1][0], "DELETE")

    def test_stop_cancels_remote_job_without_claiming_or_posting(self):
        worker, _ = self.accepted()
        worker.q.db.close()
        (self.state / "STOP").write_text("checkpoint")
        fake = Fake([("DELETE", "batch/scrape/" + JOB, (200, {"success": True, "status": "cancelled"}))])
        restarted = self.worker(fake)
        result = restarted.run(once=True)
        self.assertEqual(result["status"], "operator_checkpoint")
        self.assertEqual([call[0] for call in fake.calls], ["DELETE"])
        self.assertTrue(restarted.active_file.exists())

    def test_hostile_pagination_is_rejected_before_credential_call(self):
        for unsafe in ("https://evil.test/v2/batch/scrape/" + JOB, "https://api.firecrawl.dev:443/v2/batch/scrape/" + JOB,
            "http://api.firecrawl.dev/v2/batch/scrape/" + JOB, API + "scrape", API + "batch/scrape/" + JOB + "?skip=1&skip=2",
            "https://secret@api.firecrawl.dev/v2/batch/scrape/" + JOB):
            with self.assertRaises(ValueError):
                status_path(unsafe, JOB)
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([], "scraping", next="https://evil.test/")))])
        with self.assertRaises(ValueError):
            worker.poll()
        self.assertEqual(len(fake.calls), 3)

    def test_retry_after_is_preserved_across_rejected_posts(self):
        worker, fake = self.ready([("POST", "batch/scrape", (429, {"success": False, "error": "Rate limit", "_collector_transport": {"retry_after": "120"}})),
            ("POST", "batch/scrape", (200, {"success": True, "id": JOB}))])
        self.assertFalse(worker.submit())
        self.assertEqual(worker.job["state"], "rejected_rate_limit")
        self.assertGreaterEqual(worker.rate["next_post_at"], self.at + 121)
        self.at += 31
        self.assertFalse(worker.submit())
        self.assertEqual(len(fake.calls), 2)
        self.at = worker.rate["next_post_at"]
        self.assertTrue(worker.submit())
        self.assertTrue((worker.job_path().parent / "submission_1.json").exists())
        self.assertTrue((worker.job_path().parent / "submission_2.json").exists())

    def test_terminal_missing_results_are_held_without_resubmission(self):
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([page(RULES[0])]))),
            ("GET", "batch/scrape/" + JOB + "/errors", (200, {"errors": [], "robotsBlocked": []}))])
        self.assertFalse(worker.poll())
        self.assertEqual(worker.job["state"], "terminal_unresolved")
        self.assertEqual(worker.reason, "paused_batch_unresolved")
        self.assertTrue(worker.active_file.exists())
        self.assertEqual(worker.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_empty_running_cursor_yields_after_one_get_then_resumes_same_job(self):
        pending = poll([], "scraping", total=20, next=API + "batch/scrape/" + JOB + "?skip=0")
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, pending)),
            ("GET", "batch/scrape/" + JOB, (200, poll([page(url) for url in RULES])))])
        self.assertFalse(worker.poll())
        self.assertEqual(len(fake.calls), 3)  # credit, POST, one GET; no immediate skip=0 GET
        self.assertEqual(worker.reason, "running")
        self.assertEqual(worker.job["job_id"], JOB)
        self.assertEqual(worker.job["state"], "submitted")
        self.assertTrue(worker.active_file.exists())
        self.assertEqual(worker.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
        self.assertTrue(worker.poll())
        self.assertEqual(sum(call[0] == "POST" for call in fake.calls), 1)
        self.assertEqual(worker.run_used, 2)

    def test_repeated_validated_running_cursor_with_data_is_pending(self):
        cursor = API + "batch/scrape/" + JOB + "?skip=1"
        body = poll([page(RULES[0])], "scraping", total=2, next=cursor)
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, body)),
            ("GET", "batch/scrape/" + JOB + "?skip=1", (200, body))])
        self.assertFalse(worker.poll())
        self.assertEqual(worker.reason, "running")
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(worker.q.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.assertEqual(worker.run_used, 1)
        self.assertEqual(worker.job["job_id"], JOB)

    def test_repeated_terminal_cursor_remains_a_contract_error(self):
        cursor = API + "batch/scrape/" + JOB + "?skip=0"
        worker, fake = self.accepted([("GET", "batch/scrape/" + JOB, (200, poll([page(url) for url in RULES], next=cursor))),
            ("GET", "batch/scrape/" + JOB + "?skip=0", (200, poll([], next=cursor)))])
        with self.assertRaisesRegex(ValueError, "Repeated"):
            worker.poll()
        self.assertEqual(len(fake.calls), 4)
        self.assertTrue(worker.active_file.exists())

    def test_batch_and_single_worker_lock_path_is_exclusive(self):
        with worker_lock(self.state):
            with self.assertRaises(RuntimeError):
                with worker_lock(self.state):
                    self.fail("Second worker entered")
        with worker_lock(self.state):
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
