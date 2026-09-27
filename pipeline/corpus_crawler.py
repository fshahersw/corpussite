#!/usr/bin/env python3
"""A scoped, resumable, public-HTTP legal-corpus archiver (Python 3.10+)."""
from __future__ import annotations

import argparse
import contextlib
import csv
import dataclasses
import datetime as dt
import gzip
import hashlib
import html.parser
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from email.utils import parsedate_to_datetime
from typing import Any

VERSION = "1.0.2"
SCHEMA_VERSION = 1
CHUNK = 256 * 1024
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
ACCESS_BLOCKS = {"login_required", "access_required", "challenge", "unauthorized", "forbidden"}
ACTION_SEGMENT = re.compile(r"^(?:log[-_]?in|log[-_]?out|sign[-_]?in|sign[-_]?out|sign[-_]?up|register|registration|password|reset[-_]?password|cart|checkout|billing|payment|payments|subscribe|subscription|upgrade|delete|remove|upload|edit|admin)(?:\.(?:php|aspx?|jsp|html?))?$", re.I)
ASSET_SUFFIXES = {".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".webm"}
SAVED_HEADERS = {"content-type", "content-length", "content-disposition", "content-encoding", "etag", "last-modified", "location", "retry-after", "cache-control"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_url(value: str, base: str | None = None) -> str | None:
    """Preserve query order/escaping (including signed links), but remove fragments."""
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f]", value.strip()):
        return None
    try:
        p = urllib.parse.urlsplit(urllib.parse.urljoin(base, value.strip()) if base else value.strip())
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        hostname = p.hostname.lower().encode("idna").decode("ascii")
        hostname = f"[{hostname}]" if ":" in hostname else hostname
        port = p.port
        host = hostname if port is None or (p.scheme.lower(), port) in {("http", 80), ("https", 443)} else f"{hostname}:{port}"
        return urllib.parse.urlunsplit((p.scheme.lower(), host, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError):
        return None


def host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def path_matches(path: str, prefixes: list[str]) -> bool:
    path = urllib.parse.unquote(path)
    # Decode percent escapes before testing boundaries, and normalize dot segments.
    path = urllib.parse.urljoin("https://scope.invalid/", path).split("scope.invalid", 1)[-1]
    return any(prefix == "/" or path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


@dataclasses.dataclass
class Config:
    allow: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    workers: int = 3
    per_host_delay: float = 2.0
    timeout_seconds: float = 30.0
    max_transfer_seconds: float = 180.0
    max_response_bytes: int = 64 * 1024 * 1024
    max_extract_chars: int = 20 * 1024 * 1024
    min_free_bytes: int = 2 * 1024 * 1024 * 1024
    max_depth: int = 12
    max_retries: int = 2
    backoff_base_seconds: float = 30.0
    max_backoff_seconds: float = 3600.0
    respect_robots: bool = True
    follow_links: bool = True
    follow_external_allowed_links: bool = False
    pause_host_on_access_block: bool = True
    user_agent: str = "LegalCorpusResearch/1.0"
    shared_host_dir: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError("Unknown config keys: " + ", ".join(sorted(unknown)))
        cfg = cls(**data)
        if not isinstance(cfg.workers, int) or not 1 <= cfg.workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        if not isinstance(cfg.max_depth, int) or cfg.max_depth < 0:
            raise ValueError("max_depth must be a nonnegative integer")
        for name in ("per_host_delay", "timeout_seconds", "max_transfer_seconds", "backoff_base_seconds", "max_backoff_seconds"):
            if not isinstance(getattr(cfg, name), (int, float)) or getattr(cfg, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("max_response_bytes", "max_extract_chars"):
            if not isinstance(getattr(cfg, name), int) or getattr(cfg, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(cfg.min_free_bytes, int) or cfg.min_free_bytes < 0:
            raise ValueError("min_free_bytes must be a nonnegative integer")
        if not isinstance(cfg.max_retries, int) or cfg.max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        for name in ("respect_robots", "follow_links", "follow_external_allowed_links", "pause_host_on_access_block"):
            if not isinstance(getattr(cfg, name), bool):
                raise ValueError(f"{name} must be true or false")
        if not isinstance(cfg.user_agent, str) or not cfg.user_agent.strip() or re.search(r"[\r\n]", cfg.user_agent):
            raise ValueError("user_agent must be a nonempty, single-line string")
        if cfg.shared_host_dir is not None and not isinstance(cfg.shared_host_dir, str):
            raise ValueError("shared_host_dir must be a path string, an empty string to disable, or null for the workspace default")
        if not isinstance(cfg.allow, list):
            raise ValueError("allow must be a list")
        for rule in cfg.allow:
            if not isinstance(rule, dict) or set(rule) - {"host", "path_prefixes"}:
                raise ValueError("allow rules contain only host and path_prefixes")
            host = rule.get("host", "")
            prefixes = rule.get("path_prefixes", [])
            if not isinstance(host, str) or not host or any(c in host for c in "/@*\\"):
                raise ValueError("allow.host must be an exact host, optionally including a port")
            if not isinstance(prefixes, list) or not prefixes or any(not isinstance(p, str) or not p.startswith("/") or "?" in p or "#" in p for p in prefixes):
                raise ValueError("Every allow rule needs nonempty path_prefixes beginning with /")
            rule["host"] = host.lower()
        return cfg

    def allowed(self, url: str, scope: dict[str, Any] | None = None) -> tuple[bool, str]:
        p = urllib.parse.urlsplit(url)
        segments = urllib.parse.unquote(p.path).replace("\\", "/").split("/")
        if any(ACTION_SEGMENT.fullmatch(segment) for segment in segments):
            return False, "excluded_action"
        for key, value in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
            if key.lower() in {"action", "do", "method", "operation"} and re.search(r"delete|remove|create|edit|update|submit|upload|logout|login|purchase|checkout", value, re.I):
                return False, "excluded_action"
        if Path(p.path).suffix.lower() in ASSET_SUFFIXES:
            return False, "excluded_asset"
        if not any(host_of(url) == rule["host"] and path_matches(p.path, rule["path_prefixes"]) for rule in self.allow):
            return False, "out_of_scope"
        if scope:
            if scope.get("host") and scope["host"].lower() != host_of(url):
                return False, "outside_seed_scope"
            if scope.get("path_prefixes") and not path_matches(p.path, scope["path_prefixes"]):
                return False, "outside_seed_scope"
        return True, "allowed"


class DiskSpaceError(RuntimeError):
    pass


class PausedHost(RuntimeError):
    def __init__(self, reason: str, until: float = 0):
        super().__init__(reason)
        self.until = until


class PacingDeferred(RuntimeError):
    """Scheduling work, not an HTTP attempt or a failed retry."""

    def __init__(self, until: float):
        super().__init__("Host pacing deadline has not elapsed")
        self.until = until


class CancelledFetch(RuntimeError):
    pass


class Artifacts:
    def __init__(self, root: Path, cfg: Config):
        self.root, self.cfg, self.lock = root, cfg, threading.Lock()
        for name in ("raw", "raw_partial", "text", "metadata", "controls", "reports", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def guard(self, additional: int = 0) -> None:
        if shutil.disk_usage(self.root).free - additional < self.cfg.min_free_bytes:
            raise DiskSpaceError("Available disk space would fall below min_free_bytes")

    def write(self, relative: str, data: bytes) -> str:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.root / "tmp" / (uuid.uuid4().hex + ".tmp")
        try:
            with self.lock:
                self.guard(len(data))
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return relative

    def json(self, relative: str, data: Any) -> str:
        return self.write(relative, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def response(self, response: Any, headers: dict[str, str], stop: threading.Event) -> dict[str, Any]:
        temporary = self.root / "tmp" / (uuid.uuid4().hex + ".download")
        digest, size, started, partial, prefix = hashlib.sha256(), 0, time.monotonic(), False, b""
        try:
            with temporary.open("wb") as stream:
                while True:
                    if stop.is_set():
                        raise CancelledFetch("Run interrupted")
                    if time.monotonic() - started > self.cfg.max_transfer_seconds:
                        raise TimeoutError("Transfer exceeded max_transfer_seconds")
                    chunk = response.read(min(CHUNK, self.cfg.max_response_bytes - size + 1))
                    if not chunk:
                        break
                    if size + len(chunk) > self.cfg.max_response_bytes:
                        chunk = chunk[:self.cfg.max_response_bytes - size]
                        partial = True
                    with self.lock:
                        self.guard(len(chunk))
                        stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if len(prefix) < 32:
                        prefix += chunk[:32-len(prefix)]
                    if partial:
                        break
                stream.flush()
                os.fsync(stream.fileno())
            expected_length = headers.get("content-length", "")
            if expected_length.isdigit() and size < int(expected_length):
                partial = True
            sha = digest.hexdigest()
            mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            ext = ".pdf" if prefix.startswith(b"%PDF-") else {"text/html": ".html", "application/xhtml+xml": ".html", "text/plain": ".txt", "application/json": ".json", "application/xml": ".xml", "text/xml": ".xml", "text/csv": ".csv", "application/zip": ".zip"}.get(mime, ".bin")
            if headers.get("content-encoding", "").lower() == "gzip":
                ext = ".gz"
            relative = f"{'raw_partial' if partial else 'raw'}/{sha[:2]}/{sha}{ext}"
            target = self.root / relative
            with self.lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    temporary.unlink()
                else:
                    os.replace(temporary, target)
            return {"raw_path": relative, "sha256": sha, "byte_count": size, "raw_complete": not partial}
        finally:
            temporary.unlink(missing_ok=True)


class TextHTML(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden, self.in_title, self.title_parts = 0, False, []
        self.parts, self.links, self.anchor_stack = [], [], []
        self.description, self.base_href, self.password_form = "", "", False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag == "input" and a.get("type", "").lower() == "password":
            self.password_form = True
        if tag == "meta" and a.get("name", "").lower() == "description":
            self.description = a.get("content") or ""
        if tag == "base" and not self.base_href:
            self.base_href = a.get("href") or ""
        attribute = {"a": "href", "area": "href", "link": "href", "iframe": "src", "embed": "src", "object": "data"}.get(tag)
        if attribute and a.get(attribute):
            edge = {"href": a[attribute], "relation": "link" if tag == "a" else tag, "text": ""}
            self.links.append(edge)
            if tag == "a":
                self.anchor_stack.append(edge)
        if tag in {"p", "div", "section", "article", "header", "footer", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.anchor_stack:
            self.anchor_stack.pop()
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if not self.hidden:
            self.parts.append(data)
            if self.anchor_stack:
                self.anchor_stack[-1]["text"] += data

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())

    @property
    def text(self) -> str:
        lines = [re.sub(r"[\t \r\f\v]+", " ", line).strip() for line in "".join(self.parts).split("\n")]
        return "\n".join(line for line in lines if line)


def decode_text(data: bytes, content_type: str) -> tuple[str, str]:
    match = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", content_type, re.I)
    encoding = match.group(1) if match else "utf-8-sig"
    if not match:
        meta = re.search(br"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", data[:4096])
        if meta:
            encoding = meta.group(1).decode("ascii")
    try:
        return data.decode(encoding), encoding
    except (LookupError, UnicodeDecodeError):
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return data.decode("windows-1252", errors="replace"), "windows-1252-with-replacement"


def extract(artifacts: Artifacts, url: str, record: dict[str, Any]) -> dict[str, Any]:
    if not record.get("raw_complete"):
        return {"extraction_status": "not_attempted_partial", "links": []}
    data = (artifacts.root / record["raw_path"]).read_bytes()
    headers = record["headers"]
    mime = headers.get("content-type", "").split(";", 1)[0].lower().strip()
    encoding = headers.get("content-encoding", "").lower()
    max_chars = artifacts.cfg.max_extract_chars
    decoded_truncated = False
    if encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(max_chars * 4 + 1)
            decoded_truncated = len(data) > max_chars * 4
            data = data[:max_chars * 4]
    elif encoding not in {"", "identity"}:
        return {"extraction_status": "unsupported_content_encoding", "extraction_error": encoding, "links": []}
    result: dict[str, Any] = {"links": [], "extraction_status": "extracted", "title": ""}
    if data.startswith(b"%PDF-"):
        result["detected_type"] = "pdf"
        try:
            from pypdf import PdfReader
        except ImportError:
            return {**result, "extraction_status": "parser_unavailable", "extraction_error": "Install pypdf to extract PDF text; original PDF bytes are archived"}
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return {**result, "extraction_status": "encrypted_pdf"}
        result["page_count"] = len(reader.pages)
        result["document_metadata"] = {str(k): str(v) for k, v in dict(reader.metadata or {}).items()}
        result["title"] = str((reader.metadata or {}).get("/Title", ""))
        parts, size, pages_extracted = [], 0, 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            remaining = max_chars - size
            parts.append(page_text[:remaining])
            size += len(page_text) + 1
            pages_extracted += 1
            for annotation in page.get("/Annots", []):
                try:
                    action = annotation.get_object().get("/A", {})
                    if action.get("/URI"):
                        result["links"].append({"href": str(action["/URI"]), "relation": "pdf_annotation", "text": ""})
                except Exception:
                    pass
            if size > max_chars:
                result["extraction_status"] = "text_truncated"
                break
        result["pages_extracted"] = pages_extracted
        text = "\n".join(parts)
        if not text.strip():
            result["extraction_status"] = "no_text_ocr_needed"
    elif mime in {"text/html", "application/xhtml+xml"} or data.lstrip()[:80].lower().startswith((b"<!doctype html", b"<html")):
        source, charset = decode_text(data, headers.get("content-type", ""))
        parser = TextHTML()
        parser.feed(source)
        parser.close()
        text = parser.text
        result.update({"detected_type": "html", "title": parser.title, "description": parser.description, "charset": charset, "links": parser.links})
        base = canonical_url(parser.base_href, url) if parser.base_href else url
        for edge in result["links"]:
            edge["base_url"] = base or url
            edge["text"] = " ".join(edge["text"].split())[:2000]
        low, title = source.lower(), parser.title.lower()
        if (any(s in title for s in ("just a moment", "attention required", "access denied", "security verification", "verify you are human", "security check"))
                or "cf-chl-" in low or "please verify you are a human" in low or "verify you are human to continue" in low):
            result["access_status"] = "challenge"
        elif parser.password_form and re.search(r"\b(sign[ -]?in|log[ -]?in|login)\b", title):
            result["access_status"] = "login_required"
        elif any(s in parser.text.lower() for s in ("subscribe to view this document", "upgrade to access this document", "purchase access to this document")):
            result["access_status"] = "access_required"
    elif mime.startswith("text/") or mime in {"application/json", "application/xml", "application/ld+json", "application/rss+xml", "application/atom+xml"}:
        text, charset = decode_text(data, headers.get("content-type", ""))
        result.update({"detected_type": "text", "charset": charset})
    else:
        return {**result, "extraction_status": "unsupported_format", "detected_type": mime or "unknown"}
    if len(text) > max_chars or decoded_truncated:
        result["extraction_status"] = "text_truncated"
    text = text[:max_chars]
    result["text_char_count"] = len(text)
    # Include extractor version so future parser upgrades do not overwrite past evidence.
    result["text_path"] = artifacts.write(f"text/{record['sha256'][:2]}/{record['sha256']}.v{VERSION}.txt", text.encode("utf-8"))
    result["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return result


def shared_host_path(cfg: Config) -> Path | None:
    if cfg.shared_host_dir == "":
        return None
    workspace = Path(__file__).resolve().parents[1]
    return (workspace / (cfg.shared_host_dir or "corpus/_shared_hosts")).resolve()


def shared_host_status(cfg: Config, hosts: set[str]) -> dict[str, Any] | None:
    directory = shared_host_path(cfg)
    if directory is None:
        return None
    path = directory / "hosts.sqlite3"
    controls = []
    if path.exists():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            controls = [dict(row) for row in db.execute("SELECT * FROM host_state WHERE pause_reason IS NOT NULL OR cooldown_until>? OR next_start_at>? OR owner_token IS NOT NULL", (time.time(), time.time())) if row["host"] in hosts]
        finally:
            db.close()
    return {"directory": str(directory), "initialized": path.exists(), "relevant_host_controls": controls, "owner_note": "Recorded owners may be stale after a crash; OS locks establish active ownership."}


class SharedHosts:
    """Durable host policy plus OS-owned request locks shared by processes.

    The OS lock, not a PID or expiring lease, proves whether a request is live.
    SQL owner metadata remains after a crash so recovery adds a quiet interval
    without discarding known pacing, cooldowns, or access blocks.
    """

    def __init__(self, directory: Path, collection: Path):
        self.directory, self.collection = directory.resolve(), str(collection.resolve())
        (self.directory / "locks").mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "hosts.sqlite3"
        self.held: dict[str, tuple[Any, str]] = {}
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS host_state (
                    host TEXT PRIMARY KEY, delay_seconds REAL NOT NULL DEFAULT 0,
                    last_start_at REAL NOT NULL DEFAULT 0, next_start_at REAL NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0, pause_reason TEXT,
                    owner_token TEXT, owner_pid INTEGER, owner_collection TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, happened_at TEXT NOT NULL, kind TEXT NOT NULL,
                    host TEXT NOT NULL, collection TEXT NOT NULL, details_json TEXT NOT NULL
                );
            """)

    @contextlib.contextmanager
    def connect(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, kind: str, host: str, details: dict[str, Any]) -> None:
        db.execute("INSERT INTO events(happened_at,kind,host,collection,details_json) VALUES(?,?,?,?,?)", (utc_now(), kind, host, self.collection, js(details)))

    def _try_lock(self, host: str):
        path = self.directory / "locks" / (hashlib.sha256(host.encode()).hexdigest() + ".lock")
        stream = path.open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return None
        return stream

    @staticmethod
    def _unlock(stream) -> None:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def inspect(self, host: str) -> tuple[dict[str, Any], bool]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM host_state WHERE host=?", (host,)).fetchone()
        if host in self.held:
            return dict(row) if row else {}, True
        stream = self._try_lock(host)
        busy = stream is None
        if stream:
            self._unlock(stream)
        return dict(row) if row else {}, busy

    @staticmethod
    def _ensure(db, host: str) -> None:
        db.execute("INSERT OR IGNORE INTO host_state(host,updated_at) VALUES(?,?)", (host, utc_now()))

    def merge(self, host: str, delay: float = 0, last_start: float = 0, next_start: float = 0, cooldown: float = 0, reason: str | None = None, source: str = "observation") -> None:
        with self.connect(True) as db:
            self._ensure(db, host)
            db.execute("""UPDATE host_state SET delay_seconds=max(delay_seconds,?),last_start_at=max(last_start_at,?),
                next_start_at=max(next_start_at,?,max(last_start_at,?)*1.0+max(delay_seconds,?)),
                cooldown_until=max(cooldown_until,?),pause_reason=coalesce(nullif(pause_reason,''),?),updated_at=? WHERE host=?""",
                (delay, last_start, next_start, last_start, delay, cooldown, reason, utc_now(), host))
            self.event(db, "policy_merged", host, {"source": source, "delay_seconds": delay, "last_start_at": last_start, "next_start_at": next_start, "cooldown_until": cooldown, "pause_reason": reason})

    def reserve(self, host: str, delay: float) -> dict[str, Any]:
        if host in self.held:
            raise PacingDeferred(time.time() + 0.25)
        stream = self._try_lock(host)
        if stream is None:
            raise PacingDeferred(time.time() + 0.25)
        keep = False
        try:
            outcome, deadline, token = "ready", 0.0, uuid.uuid4().hex
            with self.connect(True) as db:
                self._ensure(db, host)
                row = dict(db.execute("SELECT * FROM host_state WHERE host=?", (host,)).fetchone())
                now = time.time()
                delay = max(delay, row["delay_seconds"])
                pacing_deadline = max(row["next_start_at"], row["last_start_at"] + delay)
                deadline = max(pacing_deadline, row["cooldown_until"])
                db.execute("UPDATE host_state SET delay_seconds=?,next_start_at=max(next_start_at,?),updated_at=? WHERE host=?", (delay, pacing_deadline, utc_now(), host))
                if row["owner_token"]:
                    # Acquiring the OS lock proves the old owner cannot still
                    # hold this request, even if its PID has since been reused.
                    pacing_deadline = max(pacing_deadline, now + delay)
                    deadline = max(pacing_deadline, row["cooldown_until"])
                    db.execute("UPDATE host_state SET owner_token=NULL,owner_pid=NULL,owner_collection=NULL,next_start_at=?,updated_at=? WHERE host=?", (pacing_deadline, utc_now(), host))
                    self.event(db, "stale_owner_recovered", host, {"previous_owner": row["owner_token"], "previous_collection": row["owner_collection"], "quiet_until": pacing_deadline})
                if row["pause_reason"]:
                    outcome = row["pause_reason"]
                elif deadline > now:
                    outcome = "deferred"
                if outcome == "ready":
                    deadline = now + delay
                    db.execute("""UPDATE host_state SET delay_seconds=?,last_start_at=?,next_start_at=?,owner_token=?,owner_pid=?,owner_collection=?,updated_at=? WHERE host=?""", (delay, now, deadline, token, os.getpid(), self.collection, utc_now(), host))
                    self.event(db, "request_reserved", host, {"owner_token": token, "pid": os.getpid(), "next_start_at": deadline})
                    row.update(last_start_at=now, next_start_at=deadline, delay_seconds=delay)
            if outcome != "ready":
                # The next scheduler inspection sees blocks/cooldowns. Races
                # after dispatch still count only as scheduling, not fetching.
                raise PacingDeferred(max(deadline, time.time() + 0.25))
            self.held[host] = (stream, token)
            keep = True
            return row
        finally:
            if not keep:
                self._unlock(stream)

    def release(self, host: str) -> None:
        held = self.held.pop(host, None)
        if held is None:
            return
        stream, token = held
        try:
            with self.connect(True) as db:
                db.execute("UPDATE host_state SET owner_token=NULL,owner_pid=NULL,owner_collection=NULL,updated_at=? WHERE host=? AND owner_token=?", (utc_now(), host, token))
        finally:
            self._unlock(stream)

    def unblock(self, host: str, reason: str) -> int:
        with self.connect(True) as db:
            count = db.execute("UPDATE host_state SET pause_reason=NULL,cooldown_until=0,updated_at=? WHERE host=?", (utc_now(), host)).rowcount
            self.event(db, "operator_unblock", host, {"reason": reason})
            return count


class Gate:
    def __init__(self, cfg: Config, states: dict[str, dict[str, Any]], stop: threading.Event, artifacts: Artifacts | None = None):
        self.cfg, self.stop, self.states = cfg, stop, states
        self.artifacts = artifacts
        self.lock = threading.Lock()
        self.next_start: dict[str, float] = {}
        self.delays: dict[str, float] = {}
        self.last_start: dict[str, float] = {}
        self.last_start_at: dict[str, float] = {}
        self.in_flight: set[str] = set()
        self.shared: SharedHosts | None = None
        if artifacts:
            self.restore_pacing()
            directory = shared_host_path(cfg)
            if directory:
                self.shared = SharedHosts(directory, artifacts.root)
                self.import_local_policy()
                self.artifacts.json("controls/shared_host_coordinator.json", {"directory": str(directory), "collection": str(artifacts.root.resolve()), "crawler_version": VERSION, "enabled_at": utc_now()})

    def import_local_policy(self) -> None:
        if self.shared:
            for host in self.states.keys() | self.last_start_at.keys() | self.delays.keys():
                state = self.states.get(host, {})
                self.shared.merge(host, self.delays.get(host, self.cfg.per_host_delay), self.last_start_at.get(host, 0),
                    time.time() + max(0, self.next_start.get(host, 0) - time.monotonic()),
                    state.get("cooldown_until", 0), state.get("pause_reason"), source="collection_checkpoint")

    def restore_pacing(self, root: Path | None = None) -> None:
        """Restore durable deadlines; legacy controls get a full quiet interval."""
        root = root or self.artifacts.root
        now, monotonic = time.time(), time.monotonic()
        path = root / "controls" / "host_pacing.json"
        saved = json.loads(path.read_text(encoding="utf-8"))["hosts"] if path.exists() else {}
        for host, state in saved.items():
            delay = max(self.cfg.per_host_delay, float(state.get("delay_seconds", 0)))
            started = float(state["last_start_at"])
            deadline = max(float(state["next_start_at"]), started + delay)
            self.delays[host] = delay
            self.last_start_at[host] = started
            self.last_start[host] = monotonic + started - now
            self.next_start[host] = monotonic + deadline - now
        # Version 1.0.0 kept crawl-delay only in memory. Reading its archived
        # robots response lets an upgrade preserve the delay without live I/O.
        for control_path in (root / "controls" / "robots").glob("*.json"):
            control = json.loads(control_path.read_text(encoding="utf-8"))
            host = host_of(control["origin"])
            delay = self.cfg.per_host_delay
            for record in control.get("observations", []):
                if not (200 <= record.get("http_status", 0) < 300 and record.get("raw_complete")):
                    continue
                raw_path = (root / record["raw_path"]).resolve()
                if not raw_path.is_relative_to(root.resolve()):
                    raise ValueError("Archived robots response path is outside the corpus root")
                source, _ = decode_text(raw_path.read_bytes(), record.get("headers", {}).get("content-type", ""))
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(source.splitlines())
                delay = max(delay, float(parser.crawl_delay(self.cfg.user_agent) or parser.crawl_delay("*") or 0))
            self.delays[host] = max(delay, self.delays.get(host, 0))
            if host not in saved:
                # Exact last-start times are unavailable in old controls. Wait
                # the entire known interval from this startup, conservatively.
                self.last_start[host], self.last_start_at[host] = monotonic, now
            self.next_start[host] = max(self.next_start.get(host, 0), self.last_start[host] + self.delays[host])

    def _persist_pacing(self) -> None:
        # Called under the gate lock before a request starts. A crash cannot
        # discard the pacing reservation that permitted an in-flight request.
        if self.artifacts:
            self.artifacts.json("controls/host_pacing.json", {"version": 1, "updated_at": utc_now(), "hosts": {
                host: {"last_start_at": started, "next_start_at": started + max(self.cfg.per_host_delay, self.delays.get(host, 0)), "delay_seconds": max(self.cfg.per_host_delay, self.delays.get(host, 0))}
                for host, started in self.last_start_at.items()
            }})

    def ready_at(self, host: str, include_cooldown: bool = True) -> float | None:
        with self.lock:
            state = self.states.get(host, {})
            cooldown = state.get("cooldown_until", 0)
            if state.get("pause_reason") or host in self.in_flight or (not include_cooldown and cooldown > time.time()):
                return None
            deadline = max(cooldown, time.time() + max(0, self.next_start.get(host, 0) - time.monotonic()))
            if self.shared:
                shared, busy = self.shared.inspect(host)
                cooldown = shared.get("cooldown_until", 0)
                if shared.get("pause_reason") or (not include_cooldown and cooldown > time.time()):
                    return None
                deadline = max(deadline, shared.get("next_start_at", 0), cooldown)
                if busy:
                    deadline = max(deadline, time.time() + 0.25)
            return deadline

    def reserve(self, host: str) -> None:
        with self.lock:
            if self.stop.is_set():
                raise CancelledFetch("Run interrupted before request")
            state = self.states.get(host, {})
            if state.get("pause_reason"):
                raise PausedHost(state["pause_reason"])
            if state.get("cooldown_until", 0) > time.time():
                raise PausedHost("host_cooldown", state["cooldown_until"])
            delay = self.next_start.get(host, 0) - time.monotonic()
            if host in self.in_flight or delay > 0:
                raise PacingDeferred(time.time() + max(0.01, delay))
            shared = self.shared.reserve(host, max(self.cfg.per_host_delay, self.delays.get(host, 0))) if self.shared else None
            try:
                self.last_start[host], self.last_start_at[host] = time.monotonic(), time.time()
                if shared:
                    self.last_start_at[host] = shared["last_start_at"]
                    self.delays[host] = max(self.delays.get(host, 0), shared["delay_seconds"])
                self.next_start[host] = self.last_start[host] + max(self.cfg.per_host_delay, self.delays.get(host, 0))
                self._persist_pacing()
            except BaseException:
                if self.shared:
                    self.shared.release(host)
                raise
            self.in_flight.add(host)

    def release(self, host: str) -> None:
        with self.lock:
            try:
                if self.shared:
                    self.shared.release(host)
            finally:
                self.in_flight.discard(host)

    def set_delay(self, host: str, delay: float) -> None:
        with self.lock:
            self.delays[host] = max(delay, self.delays.get(host, 0))
            if host in self.last_start:
                # A robots response can introduce a larger delay after its
                # own request. Apply that interval before fetching the page.
                self.next_start[host] = max(self.next_start.get(host, 0), self.last_start[host] + max(self.cfg.per_host_delay, self.delays[host]))
            if self.shared:
                self.shared.merge(host, self.delays[host], source="robots_crawl_delay")
            self._persist_pacing()

    def update(self, host: str, reason: str | None = None, until: float = 0) -> None:
        with self.lock:
            state = self.states.setdefault(host, {"pause_reason": None, "cooldown_until": 0})
            if reason:
                state["pause_reason"] = reason
            state["cooldown_until"] = max(state.get("cooldown_until", 0), until)
            if self.shared:
                self.shared.merge(host, cooldown=until, reason=reason, source="response_access_policy")

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {host: dict(state) for host, state in self.states.items()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, cfg: Config, artifacts: Artifacts, gate: Gate, stop: threading.Event):
        self.cfg, self.artifacts, self.gate, self.stop = cfg, artifacts, gate, stop
        self.robots_lock = threading.Lock()
        self.robots_cache: dict[str, tuple[Any, str, float]] = {}
        self.robots_host_locks: dict[str, threading.Lock] = {}
        self.robots_pending: dict[str, dict[str, Any]] = {}

    @contextlib.contextmanager
    def request(self, url: str):
        self.artifacts.guard()
        host = host_of(url)
        self.gate.reserve(host)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": self.cfg.user_agent, "Accept": "text/html,application/pdf,text/plain,application/json,application/xml;q=0.9,*/*;q=0.5", "Accept-Encoding": "identity"}, method="GET")
            # No cookie jar and no automatic redirects: credentials and cross-scope requests cannot leak.
            opener = urllib.request.build_opener(NoRedirect())
            started = time.monotonic()
            try:
                response = opener.open(request, timeout=self.cfg.timeout_seconds)
            except urllib.error.HTTPError as error:
                response = error
            with contextlib.closing(response):
                headers = {key.lower(): value for key, value in response.headers.items() if key.lower() in SAVED_HEADERS}
                record = {"http_status": response.code, "headers": headers, "requested_url": url, "fetched_at": utc_now()}
                record.update(self.artifacts.response(response, headers, self.stop))
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            yield record
        finally:
            self.gate.release(host)

    def retry_after(self, headers: dict[str, str], retry_count: int) -> float:
        value = headers.get("retry-after", "").strip()
        delay = min(self.cfg.max_backoff_seconds, self.cfg.backoff_base_seconds * (2 ** min(retry_count, 20)))
        if value.isdigit():
            delay = max(delay, float(value))
        elif value:
            try:
                parsed = parsedate_to_datetime(value)
                if not parsed.tzinfo:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                delay = max(delay, parsed.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
        # Never shorten a server's Retry-After to fit the local backoff cap.
        return time.time() + max(0.01, delay)

    def robots(self, url: str) -> tuple[bool, str]:
        origin = urllib.parse.urlsplit(url)
        origin_key = f"{origin.scheme}://{origin.netloc}"
        with self.robots_lock:
            host_lock = self.robots_host_locks.setdefault(origin_key, threading.Lock())
        with host_lock:
            cached = self.robots_cache.get(origin_key)
            if cached and cached[2] and cached[2] <= time.time():
                del self.robots_cache[origin_key]
            if origin_key not in self.robots_cache:
                pending = self.robots_pending.setdefault(origin_key, {"url": origin_key + "/robots.txt", "observations": []})
                robot_url = pending["url"]
                observations, parser, problem = pending["observations"], None, ""
                key = hashlib.sha256(origin_key.encode()).hexdigest()
                for _ in range(len(observations), 6):
                    with self.request(robot_url) as record:
                        observations.append(record)
                        status = record["http_status"]
                        if status in {301, 302, 303, 307, 308}:
                            target = canonical_url(record["headers"].get("location", ""), robot_url)
                            if not target or urllib.parse.urlsplit(target).hostname != origin.hostname:
                                problem = "robots_redirect_outside_host"
                                break
                            robot_url = target
                            pending["url"] = target
                            self.artifacts.json(f"controls/robots/{key}.json", {"origin": origin_key, "observations": observations, "problem": "", "in_progress": True, "checked_at": utc_now()})
                            continue
                        if status in {401, 403}:
                            problem = "robots_disallowed"
                            if self.cfg.pause_host_on_access_block:
                                self.gate.update(origin.netloc.lower(), reason="robots_disallowed")
                        elif status == 404:
                            parser = urllib.robotparser.RobotFileParser()
                            parser.parse([])
                        elif status == 429 or status >= 500:
                            until = self.retry_after(record["headers"], 0)
                            self.gate.update(origin.netloc.lower(), until=until)
                            problem = "robots_unavailable"
                        elif 200 <= status < 300 and record.get("raw_complete"):
                            raw = (self.artifacts.root / record["raw_path"]).read_bytes()
                            source, _ = decode_text(raw, record["headers"].get("content-type", ""))
                            if "<html" in source[:1000].lower() or "<!doctype html" in source[:1000].lower():
                                problem = "robots_invalid_html"
                            else:
                                parser = urllib.robotparser.RobotFileParser()
                                parser.set_url(robot_url)
                                parser.parse(source.splitlines())
                                crawl_delay = parser.crawl_delay(self.cfg.user_agent) or parser.crawl_delay("*")
                                if crawl_delay:
                                    self.gate.set_delay(origin.netloc.lower(), float(crawl_delay))
                        else:
                            problem = "robots_unavailable"
                        break
                else:
                    problem = "robots_redirect_limit"
                self.artifacts.json(f"controls/robots/{key}.json", {"origin": origin_key, "observations": observations, "problem": problem, "checked_at": utc_now()})
                expiry = time.time() + self.cfg.backoff_base_seconds if problem and problem != "robots_disallowed" else 0
                self.robots_cache[origin_key] = (parser, problem, expiry)
                del self.robots_pending[origin_key]
            parser, problem, _ = self.robots_cache[origin_key]
        if problem:
            return False, problem
        if parser is None:
            return False, "robots_unavailable"
        return (True, "allowed") if parser.can_fetch(self.cfg.user_agent, url) else (False, "robots_disallowed")

    def fetch(self, resource: dict[str, Any]) -> dict[str, Any]:
        url = resource["url"]
        record: dict[str, Any] = {"requested_url": url, "fetched_at": utc_now(), "status": "network_error", "links": [], "raw_complete": False}
        try:
            if self.cfg.respect_robots:
                allowed, reason = self.robots(url)
                if not allowed:
                    return {**record, "status": reason, "error": reason, "retryable": reason != "robots_disallowed"}
            with self.request(url) as response:
                record.update(response)
                status = record["http_status"]
                if status in {301, 302, 303, 307, 308}:
                    target = canonical_url(record["headers"].get("location", ""), url)
                    record.update(status="redirect", redirect_url=target)
                    if target:
                        record["links"] = [{"href": target, "relation": "redirect", "text": ""}]
                        segments = urllib.parse.unquote(urllib.parse.urlsplit(target).path).split("/")
                        if any(re.fullmatch(r"(?:log[-_]?in|sign[-_]?in|login|signin|auth)", segment, re.I) for segment in segments):
                            record["status"] = "login_required"
                        elif any(segment.lower() in {"upgrade", "subscribe", "subscription", "checkout"} for segment in segments):
                            record["status"] = "access_required"
                    else:
                        record.update(status="invalid_redirect", error="Missing or unsupported Location")
                elif status == 401:
                    record["status"] = "unauthorized"
                elif status == 403:
                    record["status"] = "forbidden"
                elif status == 429:
                    record.update(status="rate_limited", retryable=True, next_attempt_at=self.retry_after(record["headers"], resource["retry_count"]))
                    self.gate.update(host_of(url), until=record["next_attempt_at"])
                elif status in RETRYABLE_HTTP:
                    record.update(status="http_error", retryable=True, error=f"HTTP {status}")
                    if "retry-after" in record["headers"]:
                        record["next_attempt_at"] = self.retry_after(record["headers"], resource["retry_count"])
                        self.gate.update(host_of(url), until=record["next_attempt_at"])
                elif status in {204, 205, 304}:
                    record["status"] = "no_content"
                elif 200 <= status < 300:
                    record["status"] = "downloaded"
                elif status in {404, 410}:
                    record["status"] = "not_found"
                else:
                    record.update(status="http_error", error=f"HTTP {status}")
                if not record["raw_complete"] and 200 <= status < 300:
                    record.update(status="too_large_or_incomplete", error="Response exceeds max_response_bytes or is shorter than Content-Length")
                if record["raw_complete"] and record.get("raw_path") and not 300 <= status < 400 and record["status"] not in {"rate_limited", "no_content"}:
                    try:
                        extraction = extract(self.artifacts, url, record)
                        record.update(extraction)
                        if record["status"] == "downloaded" and extraction.get("access_status"):
                            record["status"] = extraction["access_status"]
                    except DiskSpaceError:
                        raise
                    except Exception as error:
                        record.update(extraction_status="parser_error", extraction_error=f"{type(error).__name__}: {error}")
                if record["status"] in ACCESS_BLOCKS and self.cfg.pause_host_on_access_block:
                    self.gate.update(host_of(url), reason=record["status"])
                return record
        except PacingDeferred as error:
            return {**record, "status": "pacing_deferred", "next_attempt_at": error.until}
        except PausedHost as error:
            return {**record, "status": "host_deferred", "error": str(error), "next_attempt_at": error.until}
        except CancelledFetch as error:
            return {**record, "status": "interrupted", "error": str(error)}
        except DiskSpaceError as error:
            self.stop.set()
            return {**record, "status": "disk_guard", "error": str(error)}
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError, ValueError) as error:
            return {**record, "status": "network_error", "retryable": True, "error": f"{type(error).__name__}: {error}"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contexts (
 id TEXT PRIMARY KEY, seed_url TEXT NOT NULL, source_family TEXT NOT NULL,
 jurisdiction_json TEXT NOT NULL, category TEXT NOT NULL, scope_json TEXT NOT NULL,
 seed_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, host TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', first_seen TEXT NOT NULL, updated_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
 next_attempt_at REAL NOT NULL DEFAULT 0, last_fetch_id TEXT, last_http_status INTEGER,
 raw_path TEXT, text_path TEXT, metadata_path TEXT, sha256 TEXT, byte_count INTEGER,
 raw_complete INTEGER NOT NULL DEFAULT 0, title TEXT, extraction_status TEXT,
 error TEXT, redirect_url TEXT, duplicate_of INTEGER REFERENCES resources(id)
);
CREATE INDEX IF NOT EXISTS resources_queue ON resources(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS resources_sha ON resources(sha256);
CREATE TABLE IF NOT EXISTS resource_contexts (
 resource_id INTEGER NOT NULL REFERENCES resources(id), context_id TEXT NOT NULL REFERENCES contexts(id),
 depth INTEGER NOT NULL, discovered_from TEXT, PRIMARY KEY(resource_id, context_id)
);
CREATE TABLE IF NOT EXISTS links (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES resources(id), fetch_id TEXT NOT NULL,
 target_url TEXT, raw_href TEXT NOT NULL, relation TEXT NOT NULL, anchor_text TEXT,
 decisions_json TEXT NOT NULL, observed_at TEXT NOT NULL,
 UNIQUE(fetch_id, target_url, raw_href, relation, anchor_text)
);
CREATE INDEX IF NOT EXISTS links_source ON links(source_id);
CREATE TABLE IF NOT EXISTS fetches (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, resource_id INTEGER NOT NULL REFERENCES resources(id),
 fetched_at TEXT NOT NULL, status TEXT NOT NULL, http_status INTEGER, raw_path TEXT,
 text_path TEXT, metadata_path TEXT, sha256 TEXT, byte_count INTEGER, raw_complete INTEGER,
 elapsed_seconds REAL, extraction_status TEXT, error TEXT, response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
 host TEXT PRIMARY KEY, pause_reason TEXT, cooldown_until REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, stop_reason TEXT,
 processed INTEGER NOT NULL DEFAULT 0, config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, kind TEXT NOT NULL, details_json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, root: Path, cfg: Config):
        self.root, self.cfg = root, cfg
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "corpus.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        old = self.db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if old and int(old[0]) != SCHEMA_VERSION:
            raise ValueError(f"Unsupported database schema version {old[0]}")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.db.commit()
        self.expansion: deque[tuple[int, str]] = deque()

    def close(self) -> None:
        self.db.close()

    def event(self, kind: str, details: Any) -> None:
        self.db.execute("INSERT INTO events(timestamp, kind, details_json) VALUES (?, ?, ?)", (utc_now(), kind, js(details)))

    def contexts(self, resource_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT c.*, rc.depth, rc.discovered_from FROM contexts c JOIN resource_contexts rc ON c.id=rc.context_id WHERE rc.resource_id=?", (resource_id,))]

    def enqueue(self, url: str, context: dict[str, Any], depth: int, discovered_from: str | None) -> tuple[int, bool]:
        allowed, reason = self.cfg.allowed(url, json.loads(context["scope_json"]))
        if depth > self.cfg.max_depth:
            allowed, reason = False, "depth_limit"
        now = utc_now()
        self.db.execute("INSERT OR IGNORE INTO resources(url,host,status,first_seen,updated_at,error) VALUES(?,?,?,?,?,?)", (url, host_of(url), "pending" if allowed else reason, now, now, None if allowed else reason))
        resource = self.db.execute("SELECT id,status FROM resources WHERE url=?", (url,)).fetchone()
        prior = self.db.execute("SELECT depth FROM resource_contexts WHERE resource_id=? AND context_id=?", (resource["id"], context["id"])).fetchone()
        added = not prior or depth < prior["depth"]
        self.db.execute("INSERT INTO resource_contexts(resource_id,context_id,depth,discovered_from) VALUES(?,?,?,?) ON CONFLICT(resource_id,context_id) DO UPDATE SET depth=min(depth,excluded.depth), discovered_from=CASE WHEN excluded.depth < depth THEN excluded.discovered_from ELSE discovered_from END", (resource["id"], context["id"], depth, discovered_from))
        if allowed and resource["status"] in {"out_of_scope", "outside_seed_scope", "depth_limit"}:
            self.db.execute("UPDATE resources SET status='pending', error=NULL, updated_at=? WHERE id=?", (now, resource["id"]))
        if added and allowed and resource["status"] in {"downloaded", "redirect"}:
            self.expansion.append((resource["id"], context["id"]))
        return resource["id"], added

    def ingest(self, path: Path) -> dict[str, int]:
        count, before = 0, self.db.execute("SELECT count(*) FROM resources").fetchone()[0]
        with path.open(encoding="utf-8-sig") as stream:
            for lineno, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    seed = json.loads(line)
                    if not isinstance(seed, dict):
                        raise ValueError("Each seed must be a JSON object")
                    url = canonical_url(seed.get("url", ""))
                    if not url:
                        raise ValueError("Missing or invalid HTTP(S) URL")
                    scope = seed.get("scope") or {}
                    if not isinstance(scope, dict) or set(scope) - {"host", "path_prefixes"}:
                        raise ValueError("scope contains only optional host and path_prefixes")
                    if scope:
                        Config.from_dict({"allow": [{"host": scope.get("host", host_of(url)), "path_prefixes": scope.get("path_prefixes", ["/"])}]})
                    clean = {"seed_url": url, "source_family": str(seed.get("source_family", "unspecified")), "jurisdiction_json": js(seed.get("jurisdiction", {})), "category": str(seed.get("category", "unspecified")), "scope_json": js(scope)}
                    context_id = hashlib.sha256(js(clean).encode()).hexdigest()
                    clean["id"] = context_id
                    self.db.execute("INSERT OR IGNORE INTO contexts VALUES(?,?,?,?,?,?,?,?)", (context_id, url, clean["source_family"], clean["jurisdiction_json"], clean["category"], clean["scope_json"], js(seed), utc_now()))
                    self.enqueue(url, clean, 0, seed.get("discovered_from"))
                    count += 1
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{path}:{lineno}: {error}") from error
        self.expand_saved()
        self.event("ingest", {"path": str(path.resolve()), "seed_records": count})
        self.db.commit()
        return {"seed_records": count, "new_resources": self.db.execute("SELECT count(*) FROM resources").fetchone()[0] - before}

    def link_decision(self, source_url: str, target: str | None, relation: str, context: dict[str, Any]) -> tuple[bool, str, int]:
        depth = context["depth"] + (0 if relation == "redirect" else 1)
        if not target:
            return False, "unsupported_or_invalid_url", depth
        if depth > self.cfg.max_depth:
            return False, "depth_limit", depth
        if relation != "redirect" and not self.cfg.follow_links:
            return False, "link_following_disabled", depth
        if relation != "redirect" and host_of(source_url) != host_of(target) and not self.cfg.follow_external_allowed_links:
            return False, "external_recorded_only", depth
        allowed, reason = self.cfg.allowed(target, json.loads(context["scope_json"]))
        return allowed, reason, depth

    def expand_saved(self) -> None:
        while self.expansion:
            resource_id, context_id = self.expansion.popleft()
            context = next((c for c in self.contexts(resource_id) if c["id"] == context_id), None)
            if not context:
                continue
            source = self.db.execute("SELECT url,last_fetch_id FROM resources WHERE id=?", (resource_id,)).fetchone()
            for edge in self.db.execute("SELECT target_url,relation FROM links WHERE source_id=? AND fetch_id=?", (resource_id, source["last_fetch_id"])).fetchall():
                allowed, _, depth = self.link_decision(source["url"], edge["target_url"], edge["relation"], context)
                if allowed:
                    self.enqueue(edge["target_url"], context, depth, source["url"])

    def reconcile_scope(self) -> None:
        """Re-evaluate archived links and excluded seeds after an operator changes scope."""
        for resource in self.db.execute("SELECT id,url,status FROM resources WHERE status IN ('out_of_scope','outside_seed_scope','depth_limit')").fetchall():
            for context in self.contexts(resource["id"]):
                self.enqueue(resource["url"], context, context["depth"], context["discovered_from"])
        for row in self.db.execute("SELECT rc.resource_id,rc.context_id FROM resource_contexts rc JOIN resources r ON r.id=rc.resource_id WHERE r.status IN ('downloaded','redirect')").fetchall():
            self.expansion.append((row[0], row[1]))
        self.expand_saved()
        self.event("scope_reconciled", {"config": dataclasses.asdict(self.cfg)})

    def claim(self, gate: Gate | None = None, busy_hosts: set[str] | None = None, dispatch_order: dict[str, int] | None = None) -> dict[str, Any] | None:
        busy_hosts, dispatch_order = busy_hosts or set(), dispatch_order or {}
        while True:
            # Look at one ready URL per host, not just the first URL in the
            # frontier. A large contiguous batch for one host cannot hide the
            # ready work for every host that follows it.
            candidates = self.db.execute("""SELECT r.* FROM resources r JOIN (
                SELECT min(r.id) id FROM resources r LEFT JOIN hosts h ON h.host=r.host
                WHERE r.status IN ('pending','retry_wait') AND r.next_attempt_at<=?
                AND (h.pause_reason IS NULL OR h.pause_reason='') AND coalesce(h.cooldown_until,0)<=?
                GROUP BY r.host) ready ON ready.id=r.id""", (time.time(), time.time())).fetchall()
            row = None
            for candidate in sorted(candidates, key=lambda r: (dispatch_order.get(r["host"], 0), r["id"])):
                if candidate["host"] in busy_hosts:
                    continue
                deadline = gate.ready_at(candidate["host"]) if gate else 0
                if deadline is not None and deadline <= time.time():
                    row = candidate
                    break
            if not row:
                return None
            contexts = self.contexts(row["id"])
            if not any(c["depth"] <= self.cfg.max_depth and self.cfg.allowed(row["url"], json.loads(c["scope_json"]))[0] for c in contexts):
                self.db.execute("UPDATE resources SET status='out_of_scope',error='Current config excludes every context',updated_at=? WHERE id=?", (utc_now(), row["id"]))
                self.db.commit()
                continue
            self.db.execute("UPDATE resources SET status='fetching',attempts=attempts+1,updated_at=? WHERE id=?", (utc_now(), row["id"]))
            self.db.commit()
            return dict(row)

    def next_ready_at(self, gate: Gate, wait_for_retries: bool) -> float | None:
        """Normal pacing waits always remain runnable; retry waiting is opt-in."""
        deadlines = []
        now = time.time()
        for row in self.db.execute("""SELECT r.host,min(r.next_attempt_at) resource_deadline,coalesce(h.cooldown_until,0) cooldown
            FROM resources r LEFT JOIN hosts h ON h.host=r.host
            WHERE r.status IN ('pending','retry_wait') AND (h.pause_reason IS NULL OR h.pause_reason='')
            GROUP BY r.host"""):
            retry_deadline = max(row["resource_deadline"], row["cooldown"])
            if not wait_for_retries and retry_deadline > now:
                continue
            pacing_deadline = gate.ready_at(row["host"], include_cooldown=wait_for_retries)
            if pacing_deadline is not None:
                deadlines.append(max(retry_deadline, pacing_deadline))
        return min(deadlines) if deadlines else None

    def save_result(self, resource: dict[str, Any], record: dict[str, Any], run_id: str, artifacts: Artifacts) -> None:
        if record["status"] == "pacing_deferred":
            # No page request happened. Keep prior fetch metadata and retry
            # state, undo the provisional claim count, and do not invent an
            # HTTP failure or consume a page/retry budget for this dispatch.
            self.db.execute("UPDATE resources SET status=?,attempts=max(0,attempts-1),updated_at=? WHERE id=?", (resource["status"], utc_now(), resource["id"]))
            self.db.commit()
            return
        fetch_id = uuid.uuid4().hex
        record["fetch_id"], record["run_id"] = fetch_id, run_id
        status = record["status"]
        next_attempt = record.get("next_attempt_at", 0)
        retry_count = resource["retry_count"]
        if status in {"interrupted", "host_deferred", "disk_guard"}:
            queue_status = "pending"
        elif record.get("retryable") and retry_count < self.cfg.max_retries:
            queue_status, retry_count = "retry_wait", retry_count + 1
            next_attempt = next_attempt or time.time() + min(self.cfg.max_backoff_seconds, self.cfg.backoff_base_seconds * 2 ** min(resource["retry_count"], 20))
        else:
            queue_status = status
        metadata_path = f"metadata/{fetch_id[:2]}/{fetch_id}.json"
        context_rows = self.contexts(resource["id"])
        record["contexts"] = [{"context_id": c["id"], "seed_url": c["seed_url"], "source_family": c["source_family"], "jurisdiction": json.loads(c["jurisdiction_json"]), "category": c["category"], "scope": json.loads(c["scope_json"]), "depth": c["depth"], "discovered_from": c["discovered_from"]} for c in context_rows]
        record["crawler_version"] = VERSION
        record["queue_status"] = queue_status
        try:
            artifacts.json(metadata_path, record)
        except DiskSpaceError:
            metadata_path = None
            record["metadata_write_error"] = "disk_guard; full response metadata retained in SQLite"
        duplicate = self.db.execute("SELECT id FROM resources WHERE sha256=? AND id<>? AND raw_complete=1 ORDER BY id LIMIT 1", (record.get("sha256"), resource["id"])).fetchone() if record.get("raw_complete") else None
        self.db.execute("""INSERT INTO fetches(id,run_id,resource_id,fetched_at,status,http_status,raw_path,text_path,metadata_path,sha256,byte_count,raw_complete,elapsed_seconds,extraction_status,error,response_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (fetch_id, run_id, resource["id"], record["fetched_at"], status, record.get("http_status"), record.get("raw_path"), record.get("text_path"), metadata_path, record.get("sha256"), record.get("byte_count"), int(record.get("raw_complete", False)), record.get("elapsed_seconds"), record.get("extraction_status"), record.get("error"), js(record)))
        self.db.execute("""UPDATE resources SET status=?,retry_count=?,next_attempt_at=?,updated_at=?,last_fetch_id=?,last_http_status=?,raw_path=?,text_path=?,metadata_path=?,sha256=?,byte_count=?,raw_complete=?,title=?,extraction_status=?,error=?,redirect_url=?,duplicate_of=? WHERE id=?""", (queue_status, retry_count, next_attempt, utc_now(), fetch_id, record.get("http_status"), record.get("raw_path"), record.get("text_path"), metadata_path, record.get("sha256"), record.get("byte_count"), int(record.get("raw_complete", False)), record.get("title"), record.get("extraction_status"), record.get("error"), record.get("redirect_url"), duplicate[0] if duplicate else None, resource["id"]))
        # Record the complete graph, but expand only successful pages and scoped redirects.
        for edge in record.get("links", []):
            target = canonical_url(edge["href"], edge.get("base_url") or resource["url"])
            decisions = []
            for context in context_rows:
                allowed, reason, depth = self.link_decision(resource["url"], target, edge["relation"], context)
                if status not in {"downloaded", "redirect"}:
                    allowed, reason = False, "source_not_successful"
                decisions.append({"context_id": context["id"], "decision": reason, "enqueued": allowed, "depth": depth})
                if allowed:
                    self.enqueue(target, context, depth, resource["url"])
            self.db.execute("INSERT OR IGNORE INTO links(source_id,fetch_id,target_url,raw_href,relation,anchor_text,decisions_json,observed_at) VALUES(?,?,?,?,?,?,?,?)", (resource["id"], fetch_id, target, edge["href"], edge["relation"], edge.get("text", ""), js(decisions), record["fetched_at"]))
        self.expand_saved()
        self.db.commit()

    def sync_hosts(self, states: dict[str, dict[str, Any]]) -> None:
        for host, state in states.items():
            self.db.execute("INSERT INTO hosts VALUES(?,?,?,?) ON CONFLICT(host) DO UPDATE SET pause_reason=excluded.pause_reason,cooldown_until=excluded.cooldown_until,updated_at=excluded.updated_at", (host, state.get("pause_reason"), state.get("cooldown_until", 0), utc_now()))
        self.db.commit()

    def summary(self) -> dict[str, Any]:
        statuses = {row[0]: row[1] for row in self.db.execute("SELECT status,count(*) FROM resources GROUP BY status")}
        extractions = {row[0] or "not_attempted": row[1] for row in self.db.execute("SELECT extraction_status,count(*) FROM resources GROUP BY extraction_status")}
        hosts = [dict(row) for row in self.db.execute("SELECT * FROM hosts WHERE pause_reason IS NOT NULL OR cooldown_until>?", (time.time(),))]
        pending = sum(statuses.get(s, 0) for s in ("pending", "fetching", "retry_wait"))
        downloaded = statuses.get("downloaded", 0)
        terminal_failures = sum(value for key, value in statuses.items() if key not in {"downloaded", "redirect", "pending", "fetching", "retry_wait", "no_content"})
        resolved_redirects = self.db.execute("""WITH RECURSIVE chains(start_id,redirect_url,step,seen) AS (
            SELECT id,redirect_url,0,printf(',%d,',id) FROM resources WHERE status='redirect'
            UNION ALL SELECT chains.start_id,r.redirect_url,chains.step+1,chains.seen||r.id||','
            FROM chains JOIN resources r ON r.url=chains.redirect_url
            WHERE chains.step<64 AND r.status='redirect' AND instr(chains.seen,printf(',%d,',r.id))=0
        ) SELECT count(DISTINCT start_id) FROM chains JOIN resources target ON target.url=chains.redirect_url
        WHERE target.status IN ('downloaded','no_content')""").fetchone()[0]
        unresolved_redirects = statuses.get("redirect", 0) - resolved_redirects
        return {"generated_at": utc_now(), "root": str(self.root.resolve()), "crawler_version": VERSION, "resource_count": sum(statuses.values()), "contexts": self.db.execute("SELECT count(*) FROM contexts").fetchone()[0], "fetch_attempts": self.db.execute("SELECT count(*) FROM fetches").fetchone()[0], "link_observations": self.db.execute("SELECT count(*) FROM links").fetchone()[0], "statuses": statuses, "extraction_statuses": extractions, "pending_resources": pending, "downloaded_resources": downloaded, "terminal_failures_or_exclusions": terminal_failures, "unresolved_redirects": unresolved_redirects, "host_blocks_or_cooldowns": hosts, "shared_host_coordinator": shared_host_status(self.cfg, {row[0] for row in self.db.execute("SELECT DISTINCT host FROM resources")}), "unique_complete_payloads": self.db.execute("SELECT count(DISTINCT sha256) FROM resources WHERE raw_complete=1").fetchone()[0], "duplicate_resources": self.db.execute("SELECT count(*) FROM resources WHERE duplicate_of IS NOT NULL").fetchone()[0], "frontier_exhausted": pending == 0, "all_enqueued_resources_downloaded": pending == 0 and terminal_failures == 0 and unresolved_redirects == 0, "full_source_corpus_complete": False, "completeness_note": "Exhausting discovered URLs does not prove full source coverage. Authenticated, unlinked, dynamically loaded, unavailable and excluded records require separate enumeration and reconciliation."}


@contextlib.contextmanager
def run_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".crawler.lock"
    stream = path.open("a+b")
    acquired = False
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            raise RuntimeError(f"Another crawler mutation owns {path}; use status/export while it runs") from error
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


def run_crawl(store: Store, artifacts: Artifacts, max_pages: int = 100, max_seconds: float = 600, wait_for_retries: bool = False) -> dict[str, Any]:
    cfg, stop = store.cfg, threading.Event()
    gate = Gate(cfg, {row["host"]: dict(row) for row in store.db.execute("SELECT * FROM hosts")}, stop, artifacts)
    fetcher = Fetcher(cfg, artifacts, gate, stop)
    run_id, started = uuid.uuid4().hex, time.monotonic()
    config_snapshot = js(dataclasses.asdict(cfg))
    previous_run = store.db.execute("SELECT config_json FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    recovered = store.db.execute("UPDATE resources SET status='pending',updated_at=? WHERE status='fetching'", (utc_now(),)).rowcount
    if previous_run and previous_run[0] != config_snapshot:
        store.reconcile_scope()
    store.db.execute("INSERT INTO runs(id,started_at,config_json) VALUES(?,?,?)", (run_id, utc_now(), config_snapshot))
    if recovered:
        store.event("recovered_interrupted_frontier", {"count": recovered, "run_id": run_id})
    store.db.commit()
    active, processed, submitted, stop_reason = {}, 0, 0, "frontier_exhausted"
    dispatch_order: dict[str, int] = {}
    dispatch_count = 0
    executor = ThreadPoolExecutor(max_workers=cfg.workers, thread_name_prefix="corpus-fetch")
    last_progress = 0.0
    try:
        while True:
            elapsed = time.monotonic() - started
            if max_seconds and elapsed >= max_seconds:
                stop_reason = "time_budget"
                stop.set()
            if stop.is_set() and stop_reason == "frontier_exhausted":
                stop_reason = "interrupted_or_disk_guard"
            if time.monotonic() - last_progress >= 10:
                print(js({"run_id": run_id, "processed": processed, "in_flight": len(active), "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
                last_progress = time.monotonic()
            while not stop.is_set() and len(active) < cfg.workers and (not max_pages or submitted < max_pages):
                resource = store.claim(gate, {resource["host"] for resource in active.values()}, dispatch_order)
                if not resource:
                    break
                active[executor.submit(fetcher.fetch, resource)] = resource
                dispatch_count += 1
                dispatch_order[resource["host"]] = dispatch_count
                submitted += 1
            if not active:
                if stop.is_set():
                    break
                if max_pages and submitted >= max_pages:
                    stop_reason = "page_budget"
                    break
                remaining = store.db.execute("SELECT count(*) FROM resources WHERE status IN ('pending','retry_wait')").fetchone()[0]
                if not remaining:
                    break
                eligible_future = store.next_ready_at(gate, wait_for_retries)
                if eligible_future is None:
                    stop_reason = "blocked_hosts" if wait_for_retries else "deferred_or_blocked_frontier"
                    break
                stop.wait(min(1.0, max(0.01, eligible_future - time.time())))
                continue
            done, _ = wait(active, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                resource = active.pop(future)
                try:
                    record = future.result()
                except Exception as error:
                    record = {"requested_url": resource["url"], "fetched_at": utc_now(), "status": "internal_error", "error": f"{type(error).__name__}: {error}", "raw_complete": False, "links": []}
                store.save_result(resource, record, run_id, artifacts)
                store.sync_hosts(gate.snapshot())
                if record["status"] == "pacing_deferred":
                    submitted -= 1
                else:
                    processed += 1
                if record["status"] == "disk_guard":
                    stop_reason = "disk_guard"
                    stop.set()
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        stop.set()
    except Exception:
        stop_reason = "internal_error"
        stop.set()
        raise
    finally:
        stop.set()
        # Complete/persist already-running requests before releasing the exclusive mutation lock.
        executor.shutdown(wait=True, cancel_futures=False)
        for future, resource in active.items():
            try:
                record = future.result()
                store.save_result(resource, record, run_id, artifacts)
                if record["status"] != "pacing_deferred":
                    processed += 1
            except Exception as error:
                store.db.execute("UPDATE resources SET status='pending',error=?,updated_at=? WHERE id=?", (f"Interrupted result persistence: {type(error).__name__}", utc_now(), resource["id"]))
        store.sync_hosts(gate.snapshot())
        store.db.execute("UPDATE runs SET ended_at=?,stop_reason=?,processed=? WHERE id=?", (utc_now(), stop_reason, processed, run_id))
        store.db.commit()
        summary = {**store.summary(), "run_id": run_id, "run_processed": processed, "stop_reason": stop_reason}
        try:
            artifacts.json("checkpoint.json", summary)
        except DiskSpaceError:
            pass
    return summary


def export_reports(store: Store, artifacts: Artifacts) -> dict[str, Any]:
    report_dir = store.root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    # A single read transaction gives all exports a consistent SQLite snapshot during a run.
    store.db.execute("BEGIN")
    try:
        for table in ("resources", "contexts", "resource_contexts", "links", "fetches", "hosts", "runs", "events"):
            destination = report_dir / (table + ".jsonl")
            temporary = store.root / "tmp" / (uuid.uuid4().hex + ".export")
            count = 0
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                    for row in store.db.execute(f"SELECT * FROM {table}"):
                        record = dict(row)
                        for key in list(record):
                            if key.endswith("_json"):
                                record[key[:-5]] = json.loads(record.pop(key))
                        line = js(record) + "\n"
                        artifacts.guard(len(line.encode("utf-8")))
                        stream.write(line)
                        count += 1
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            counts[table] = count
        temporary = store.root / "tmp" / (uuid.uuid4().hex + ".csv")
        try:
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                cursor = store.db.execute("SELECT id,url,status,last_http_status,sha256,byte_count,raw_complete,raw_path,text_path,extraction_status,duplicate_of,error FROM resources ORDER BY id")
                writer = csv.writer(stream)
                writer.writerow([item[0] for item in cursor.description])
                for row in cursor:
                    # Prevent spreadsheet formula evaluation in third-party scraped strings.
                    values = ["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in row]
                    artifacts.guard(8192)
                    writer.writerow(values)
            os.replace(temporary, report_dir / "resources.csv")
        finally:
            temporary.unlink(missing_ok=True)
        summary = store.summary()
        artifacts.json("reports/summary.json", summary)
        return {"exported_at": utc_now(), "directory": str(report_dir.resolve()), "rows": counts, "summary": summary}
    finally:
        store.db.rollback()


def load_config(root: Path, config_path: str | None = None) -> Config:
    path = Path(config_path) if config_path else root / "config.json"
    if config_path and not path.is_file():
        raise FileNotFoundError(f"Config file does not exist: {path}")
    return Config.from_dict(json.loads(path.read_text(encoding="utf-8-sig"))) if path.exists() else Config()


def import_shared_collections(directory: Path, collections: list[Path]) -> dict[str, Any]:
    """Offline union of existing controls; source collection SQLite is read-only."""
    results = []
    for collection in collections:
        collection = collection.resolve()
        cfg = load_config(collection)
        cfg.shared_host_dir = ""
        db_path = collection / "corpus.sqlite3"
        db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            states = {row["host"]: dict(row) for row in db.execute("SELECT * FROM hosts")}
            active = db.execute("SELECT count(*) FROM resources WHERE status='fetching'").fetchone()[0]
        finally:
            db.close()
        gate = Gate(cfg, states, threading.Event())
        gate.restore_pacing(collection)
        gate.shared = SharedHosts(directory, collection)
        gate.import_local_policy()
        results.append({"collection": str(collection), "hosts_imported": len(gate.states.keys() | gate.last_start_at.keys() | gate.delays.keys()), "saved_fetching_rows": active})
    return {"shared_directory": str(directory), "collections": results, "note": "Saved fetching rows do not establish whether a process is alive. Stop/checkpoint pre-1.0.2 workers before overlapping new runs; keep the full known host delay after termination."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Corpus output directory; state and artifacts stay beneath this path")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Add JSONL seeds without fetching")
    ingest.add_argument("--seeds", type=Path, required=True)
    ingest.add_argument("--config", help="JSON allowlist/config; saved under the corpus root")
    run = sub.add_parser("run", help="Process the persisted queue")
    run.add_argument("--config", help="Optional config override; saved as the new config")
    run.add_argument("--max-pages", type=int, default=100, help="Maximum resource attempts this run; 0 means no page limit")
    run.add_argument("--max-seconds", type=float, default=600, help="Run wall-clock budget; 0 means no limit (in-flight requests drain)")
    run.add_argument("--wait-for-retries", action="store_true", help="Wait for scheduled backoff within run budgets; otherwise checkpoint and exit")
    sub.add_parser("status", help="Read queue and completeness metrics")
    sub.add_parser("export", help="Export snapshot JSONL, summary JSON and spreadsheet-safe CSV")
    retry = sub.add_parser("retry", help="Explicitly requeue selected outcomes; does not clear host pauses")
    retry.add_argument("--status", action="append", required=True, help="Exact status; repeat for several")
    retry.add_argument("--host", help="Optional exact host filter")
    unblock = sub.add_parser("unblock-host", help="Clear a saved host pause/cooldown after an operator resolves access")
    unblock.add_argument("--host", required=True)
    unblock.add_argument("--reason", required=True, help="Access/environment change justifying a new attempt; recorded in the audit log")
    shared_import = sub.add_parser("shared-host-import", help="Offline merge of saved collection pacing, robots delay and host policies into shared coordination")
    shared_import.add_argument("--collection-root", type=Path, action="append", required=True)
    shared_unblock = sub.add_parser("shared-host-unblock", help="Explicitly clear a shared access block/cooldown; leaves collection controls and pacing intact")
    shared_unblock.add_argument("--host", required=True)
    shared_unblock.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "run" and (args.max_pages < 0 or args.max_seconds < 0):
        parser.error("Run budgets must be nonnegative")
    root = args.root.resolve()
    store = None
    try:
        config_path = getattr(args, "config", None)
        cfg = load_config(root, config_path)
        if args.command in {"shared-host-import", "shared-host-unblock"}:
            directory = shared_host_path(cfg)
            if directory is None:
                raise ValueError("Shared coordination is disabled in this root's config")
            if args.command == "shared-host-import":
                result = import_shared_collections(directory, args.collection_root)
            else:
                result = {"shared_hosts_updated": SharedHosts(directory, root).unblock(args.host.lower(), args.reason), "note": "Local collection pauses remain in force; explicit local unblock-host actions may also be needed."}
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
            return 0
        artifacts = Artifacts(root, cfg)
        with (run_lock(root) if args.command in {"ingest", "run", "retry", "unblock-host"} else contextlib.nullcontext()):
            store = Store(root, cfg)
            if config_path:
                artifacts.json("config.json", dataclasses.asdict(cfg))
            if args.command == "ingest":
                result = store.ingest(args.seeds)
            elif args.command == "run":
                if not cfg.allow:
                    raise ValueError("No allow rules configured; add exact allowed hosts/path prefixes before running")
                result = run_crawl(store, artifacts, args.max_pages, args.max_seconds, args.wait_for_retries)
            elif args.command == "status":
                result = store.summary()
            elif args.command == "export":
                result = export_reports(store, artifacts)
            elif args.command == "retry":
                if any(status in {"pending", "fetching", "downloaded", "redirect"} for status in args.status):
                    raise ValueError("retry targets error, exclusion or deferred statuses, not active or successful work")
                placeholders = ",".join("?" for _ in args.status)
                parameters = [utc_now(), *args.status]
                query = f"UPDATE resources SET status='pending',retry_count=0,next_attempt_at=0,error=NULL,updated_at=? WHERE status IN ({placeholders})"
                if args.host:
                    query += " AND host=?"
                    parameters.append(args.host.lower())
                count = store.db.execute(query, parameters).rowcount
                store.event("manual_retry", {"statuses": args.status, "host": args.host, "count": count})
                store.db.commit()
                result = {"requeued": count, "note": "Host pauses remain in force until explicitly cleared"}
            else:
                host = args.host.lower()
                count = store.db.execute("UPDATE hosts SET pause_reason=NULL,cooldown_until=0,updated_at=? WHERE host=?", (utc_now(), host)).rowcount
                store.event("unblock_host", {"host": host, "reason": args.reason, "updated": count})
                store.db.commit()
                result = {"hosts_updated": count}
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as error:
        print(js({"error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
