"""Build the focused county delivery offline from explicit saved source evidence."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
COUNTY_COLLECTIONS = ("corpus/county_sites", "corpus/county_entries_resume_20260913", "corpus/county_entries_continuation_20260913", "corpus/county_registry_continuation_20260913")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_database(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def read_county_snapshots(root):
    """Read the explicitly supported county collections, with ID namespaces."""
    snapshots = {}
    for collection in COUNTY_COLLECTIONS:
        path = root / collection / "corpus.sqlite3"
        if not path.exists():
            if collection == COUNTY_COLLECTIONS[0]:
                raise FileNotFoundError(path)
            continue
        with closing(read_database(path)) as db:
            resources = [dict(row) for row in db.execute("SELECT r.id,r.url,r.status,r.last_http_status,r.raw_complete,r.raw_path,r.sha256,r.text_path,r.metadata_path,r.extraction_status,r.last_fetch_id,f.fetched_at FROM resources r LEFT JOIN fetches f ON f.id=r.last_fetch_id ORDER BY r.id")]
            contexts = [dict(row) for row in db.execute("SELECT id,seed_url,source_family,jurisdiction_json,seed_json FROM contexts ORDER BY id")]
            associations = [dict(row) for row in db.execute("SELECT resource_id,context_id FROM resource_contexts ORDER BY resource_id,context_id")]
        for row in contexts:
            row["jurisdiction"] = json.loads(row.pop("jurisdiction_json"))
            seed = json.loads(row.pop("seed_json"))
            row["source_url_basis"] = seed.get("source_url_basis", "recorded_collector_seed_url")
            if collection in COUNTY_COLLECTIONS[1:] and row["source_family"] == "derived_county_entry_from_observed_robots_redirect_origin":
                proof = seed["provenance"]
                source, target = urlsplit(proof["source_entry_url"]), urlsplit(row["seed_url"])
                redirect = urlsplit(proof["observed_redirect_target"])
                assert source.path == target.path and source.query == target.query
                assert source.hostname != target.hostname and source.hostname.removeprefix("www.") == target.hostname.removeprefix("www.")
                assert (target.scheme, target.netloc) == (redirect.scheme, redirect.netloc) and redirect.path == "/robots.txt"
                assert seed["entry_url_observed_as_redirect_target"] is False and seed["known_access_denial_in_reviewed_evidence"] is False
                lineage = {key: proof[key] for key in ("source_collection", "source_resource_id", "source_entry_url", "robots_control_path", "robots_control_sha256", "requested_robots_url", "observed_redirect_target", "raw_path", "raw_sha256")}
                lineage.update(derived_seed_url=row["seed_url"], entry_url_derivation=seed["entry_url_derivation"], entry_url_observed_as_redirect_target=False)
                row["derived_entry_lineage"] = lineage
            elif collection in COUNTY_COLLECTIONS[1:] and row["source_family"] == "observed_county_entry_from_same_domain_http_redirect":
                proof = seed["provenance"]
                source, target = urlsplit(proof["source_entry_url"]), urlsplit(row["seed_url"])
                assert source.scheme in {"http", "https"} and target.scheme in {"http", "https"}
                assert source.hostname != target.hostname and source.hostname.removeprefix("www.") == target.hostname.removeprefix("www.")
                assert row["seed_url"] == proof["observed_redirect_target"]
                assert seed["entry_url_observed_as_redirect_target"] is True and seed["known_access_denial_in_reviewed_evidence"] is False
                metadata_path = (root / proof["redirect_metadata_path"]).resolve()
                assert metadata_path.is_relative_to(root.resolve())
                metadata_bytes = metadata_path.read_bytes()
                assert hashlib.sha256(metadata_bytes).hexdigest() == proof["redirect_metadata_sha256"]
                metadata = json.loads(metadata_bytes)
                assert metadata["http_status"] == proof["http_status"] and proof["http_status"] in {301, 302, 303, 307, 308}
                assert metadata["requested_url"] == proof["requested_url"] == proof["source_entry_url"]
                assert metadata["redirect_url"] == proof["observed_redirect_target"]
                assert metadata["headers"]["location"] == proof["location_header"]
                assert metadata["sha256"] == proof["raw_sha256"]
                lineage = {key: proof[key] for key in ("source_collection", "source_resource_id", "source_entry_url", "redirect_metadata_path", "redirect_metadata_sha256", "requested_url", "http_status", "location_header", "observed_redirect_target", "raw_path", "raw_sha256")}
                lineage.update(observed_seed_url=row["seed_url"], entry_url_observed_as_redirect_target=True)
                row["observed_redirect_lineage"] = lineage
        snapshots[collection] = {"resources": resources, "contexts": contexts, "resource_contexts": associations}
    return snapshots


def county_seed_join(snapshots):
    """Keep repeated numeric IDs and shared-URL geography contexts separate."""
    resources, by_seed = {}, defaultdict(lambda: defaultdict(list))
    for collection, snapshot in snapshots.items():
        contexts = {row["id"]: row for row in snapshot["contexts"]}
        for row in snapshot["resources"]:
            resources[(collection, row["id"])] = row
        for link in snapshot["resource_contexts"]:
            context = contexts[link["context_id"]]
            key = (collection, link["resource_id"])
            assert key in resources
            by_seed[context["seed_url"]][key].append(context)
            if context.get("derived_entry_lineage"):
                # The original href stays unchanged; its new capture association
                # carries an explicit robots-origin derivation, never a page-href claim.
                by_seed[context["derived_entry_lineage"]["source_entry_url"]][key].append(context)
            if context.get("observed_redirect_lineage"):
                by_seed[context["observed_redirect_lineage"]["source_entry_url"]][key].append(context)
    return resources, by_seed


class Delivery:
    def __init__(self, root, output):
        self.root, self.output = root.resolve(), output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.inputs, self.references, self.issues = [], {}, []
        self.captures = {}

    def reference(self, value, expected_sha=None):
        path = (self.root / str(value).replace("\\", "/")).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Reference outside workspace: " + str(value))
        relative = path.relative_to(self.root).as_posix()
        if relative not in self.references:
            result = {"path": relative, "exists": path.is_file(), "sha256": None, "bytes": None}
            if result["exists"]:
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(block)
                result.update(sha256=hasher.hexdigest(), bytes=path.stat().st_size)
            self.references[relative] = result
        result = self.references[relative]
        if not result["exists"]:
            issue = {"kind": "missing_file", "path": relative}
            if issue not in self.issues:
                self.issues.append(issue)
        elif expected_sha and result["sha256"] != expected_sha:
            issue = {"kind": "hash_mismatch", "path": relative, "expected_sha256": expected_sha, "actual_sha256": result["sha256"]}
            if issue not in self.issues:
                self.issues.append(issue)
        return relative

    def file_input(self, name, kind="json"):
        path = self.root / name
        relative = self.reference(name)
        self.inputs.append({"input_type": "saved_file", **self.references[relative]})
        if kind == "jsonl":
            return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if kind == "csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def table(self, stem, rows, columns=None):
        columns = columns or sorted({key for row in rows for key in row})
        json_path, csv_path = self.output / (stem + ".jsonl"), self.output / (stem + ".csv")
        with json_path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(canonical(row) + "\n")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: canonical(row[key]) if isinstance(row.get(key), (list, dict)) else row.get(key) for key in columns})

    def json(self, name, value):
        (self.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def capture(self, item, base="", locator=None):
        raw_value = item.get("raw_path")
        if not raw_value or not item.get("raw_sha256"):
            return None
        raw = self.reference(str(Path(base) / raw_value), item["raw_sha256"])
        raw_result = self.references[raw]
        text = self.reference(str(Path(base) / item["text_path"]), item.get("text_file_sha256")) if item.get("text_path") else None
        metadata = self.reference(str(Path(base) / item["metadata_path"])) if item.get("metadata_path") else None
        capture_id = "capture_" + value_hash([item["source_url"], item["raw_sha256"], item["capture_kind"]])[:24]
        text_characters = len((self.root / text).read_text(encoding="utf-8-sig").strip()) if text and self.references[text]["exists"] else 0
        content_availability = "empty_response" if raw_result.get("bytes") == 0 else "nonempty_saved_text" if text_characters else "raw_saved_without_usable_text"
        evidence = {"capture_id": capture_id, "source_url": item["source_url"], "capture_kind": item["capture_kind"],
            "collection": item.get("collection"), "retrieved_at": item.get("retrieved_at"),
            "raw_path": raw, "raw_sha256": item["raw_sha256"], "raw_bytes": raw_result["bytes"],
            "text_path": text, "text_sha256": self.references[text]["sha256"] if text else None,
            "trimmed_text_characters": text_characters, "content_availability": content_availability,
            "metadata_path": metadata, "extraction_status": item.get("extraction_status"),
            "saved_status": "saved_hash_verified" if raw_result["exists"] and raw_result["sha256"] == item["raw_sha256"] else "saved_reference_invalid",
            "provenance_locators": [locator] if locator else []}
        if capture_id in self.captures:
            self.captures[capture_id]["provenance_locators"] = sorted(set(self.captures[capture_id]["provenance_locators"] + evidence["provenance_locators"]))
        else:
            self.captures[capture_id] = evidence
        return capture_id

    def build(self):
        geography = self.file_input("reports/geography/summary.json")
        census = self.file_input("reports/geography/input_snapshots/census.json")
        reconciliation = self.file_input("reports/geography/county_reconciliation.jsonl", "jsonl")
        special_source = self.file_input("reports/geography/non_census_jurisdictions.jsonl", "jsonl")
        ambiguity_source = self.file_input("reports/geography/county_ambiguities.jsonl", "jsonl")
        profiles_source = self.file_input("reports/geography/input_snapshots/county_profiles.csv", "csv")
        websites_source = self.file_input("reports/geography/website_associations.jsonl", "jsonl")
        registry = self.file_input("sources/official_courts/datasets/cisa_county_government_domains.json")
        census_provenance = self.file_input("sources/official_courts/datasets/2026_Gaz_counties_national.provenance.json")
        self.reference("sources/official_courts/" + census_provenance["raw_archive_path"], census_provenance["archive_sha256"])
        assert len(census) == 3144 and len({row["geoid"] for row in census}) == 3144
        assert len(special_source) == geography["non_census_jurisdictions"]
        assert len(ambiguity_source) == geography["ambiguous_profiles"]
        assert self.references["reports/geography/input_snapshots/census.json"]["sha256"] == geography["inputs"]["census"]["sha256"]
        geoids = {row["geoid"] for row in census}
        for county in census:
            assert re.fullmatch(r"\d{5}", county["geoid"]) and county["geoid"] == county["state_fips"] + county["county_fips"]
        profile_urls = {row["source_url"] for row in reconciliation}
        website_urls = {row["canonical_url"] for row in websites_source if row.get("canonical_url")} | {row["official_url"] for row in registry}
        document_db = self.root / "catalog/documents.sqlite3"
        with closing(read_database(document_db)) as db:
            query = "SELECT version_id,collection,source_url,capture_kind,raw_path,raw_sha256,text_path,text_file_sha256,metadata_path,retrieved_at,extraction_status FROM latest_documents WHERE capture_kind IN ('provider_rendered_public_page','direct_public_capture')"
            document_rows = [dict(row) for row in db.execute(query) if row["source_url"] in profile_urls or row["source_url"] in website_urls]
        document_rows.sort(key=lambda row: (row["source_url"], row["capture_kind"], row["version_id"]))
        self.inputs.append({"input_type": "read_only_sql_snapshot", "path": "catalog/documents.sqlite3", "query": query,
            "selection": "Only exact saved profile and website URLs from the explicit geography/registry inputs", "rows": len(document_rows), "selected_rows_sha256": value_hash(document_rows)})
        captures_by_url = defaultdict(list)
        for row in document_rows:
            capture_id = self.capture(row, locator="catalog/documents.sqlite3#versions/" + row["version_id"])
            if capture_id:
                captures_by_url[row["source_url"]].append(capture_id)

        snapshots = read_county_snapshots(self.root)
        snapshot_paths = []
        for collection, sql_snapshot in snapshots.items():
            self.inputs.append({"input_type": "read_only_sql_snapshot", "path": collection + "/corpus.sqlite3",
                "tables": list(sql_snapshot), "row_counts": {key: len(value) for key, value in sql_snapshot.items()}, "selected_rows_sha256": value_hash(sql_snapshot)})
            name = "county_site_snapshot.json" if collection == COUNTY_COLLECTIONS[0] else Path(collection).name + "_snapshot.json"
            self.json(name, sql_snapshot)
            snapshot_paths.append("delivery/focused_legal_corpus/counties/" + name)
        resource_map, resources_by_seed = county_seed_join(snapshots)
        resources = list(resource_map.values())
        resource_captures = {}
        for resource_key, row in resource_map.items():
            collection, resource_id = resource_key
            if row["status"] == "downloaded" and row["raw_complete"] == 1 and row["last_http_status"] is not None and 200 <= row["last_http_status"] < 300:
                resource_captures[resource_key] = self.capture({"source_url": row["url"], "raw_path": row["raw_path"], "raw_sha256": row["sha256"],
                    "text_path": row["text_path"], "metadata_path": row["metadata_path"], "capture_kind": "direct_public_capture", "collection": collection,
                    "retrieved_at": row["fetched_at"], "extraction_status": row["extraction_status"]}, base=collection,
                    locator=collection + "/corpus.sqlite3#resources/" + str(resource_id))

        def site_collection(url):
            associated = resources_by_seed.get(url, {})
            counts = Counter(resource_map[resource_id]["status"] for resource_id in associated)
            links = []
            seed_host = urlsplit(url).hostname or ""
            for resource_key, contexts in sorted(associated.items()):
                capture_id = resource_captures.get(resource_key)
                if capture_id:
                    actual_host = urlsplit(resource_map[resource_key]["url"]).hostname or ""
                    context_ids = sorted({context["id"] for context in contexts})
                    context_geoids = sorted({context["jurisdiction"]["geoid"] for context in contexts if context["jurisdiction"].get("geoid")})
                    lineages = {value_hash(context["derived_entry_lineage"]): context["derived_entry_lineage"] for context in contexts if context.get("derived_entry_lineage")}
                    observed_lineages = {value_hash(context["observed_redirect_lineage"]): context["observed_redirect_lineage"] for context in contexts if context.get("observed_redirect_lineage")}
                    for lineage in lineages.values():
                        self.reference(lineage["robots_control_path"], lineage["robots_control_sha256"])
                        self.reference(lineage["raw_path"], lineage["raw_sha256"])
                    for lineage in observed_lineages.values():
                        self.reference(lineage["redirect_metadata_path"], lineage["redirect_metadata_sha256"])
                        self.reference(lineage["raw_path"], lineage["raw_sha256"])
                    relation = "observed_same_domain_http_redirect" if observed_lineages else "derived_from_observed_robots_redirect_origin" if lineages else "collector_seed_context_only"
                    links.append({"capture_id": capture_id, "relation": relation, "context_id": context_ids[0],
                        "context_ids": context_ids, "context_geoids": context_geoids, "resource_collection": resource_key[0], "resource_id": resource_key[1],
                        "context_source_url_bases": sorted({context["source_url_basis"] for context in contexts}),
                        "derived_entry_lineage": [lineages[key] for key in sorted(lineages)],
                        "observed_redirect_lineage": [observed_lineages[key] for key in sorted(observed_lineages)],
                        "same_host_or_exact_www_alias": actual_host.removeprefix("www.") == seed_host.removeprefix("www."), "county_authority_verified": False})
            known = {row["capture_id"] for row in links}
            for capture_id in captures_by_url.get(url, []):
                if capture_id not in known and self.captures[capture_id]["capture_kind"] == "direct_public_capture":
                    links.append({"capture_id": capture_id, "relation": "exact_saved_source_url", "county_authority_verified": False})
            return {"collection_status_counts": dict(sorted(counts.items())), "county_site_context_present": bool(associated),
                    "saved_capture_refs": sorted(links, key=lambda row: row["capture_id"])}

        profile_map = {}
        for number, row in enumerate(reconciliation, 1):
            capture_ids = sorted(set(captures_by_url.get(row["source_url"], [])))
            profile_id = "profile_" + value_hash(row["source_url"])[:24]
            item = {**row, "profile_id": profile_id, "reconciliation_locator": "reports/geography/county_reconciliation.jsonl:" + str(number),
                "saved_profile_status": "saved_profile_capture" if capture_ids else "profile_metadata_without_indexed_capture", "capture_ids": capture_ids,
                "raw_paths": sorted({self.captures[capture_id]["raw_path"] for capture_id in capture_ids}),
                "text_paths": sorted({self.captures[capture_id]["text_path"] for capture_id in capture_ids if self.captures[capture_id]["text_path"]})}
            profile_map[row["source_url"]] = item
        assert len(profile_map) == len(reconciliation) == len(profiles_source)

        website_evidence = []
        for number, row in enumerate(websites_source, 1):
            url = row.get("canonical_url") or row.get("observed_url")
            item = {"website_evidence_id": "website_" + value_hash(["trellis", number, row["source_url"], url])[:24], "evidence_family": "trellis_reported_website",
                "url": url, "source_profile_url": row["source_url"], "assigned_geoid": row["census_geoid"] if row["county_association_status"] == "matched" else None,
                "candidate_geoids": [], "county_association_status": row["county_association_status"], "county_association_reason": row["county_association_reason"],
                "site_authority_verified": False, "authority_classification": row["authority_classification"], "authority_note": row["authority_note"],
                "government_namespace_signal": row["government_namespace_signal"], "website_validation_status": row["website_validation_status"],
                "displayed_value": row["displayed_value"], "display_differs_from_href": row["display_differs_from_href"],
                "provenance_locator": "reports/geography/website_associations.jsonl:" + str(number),
                "profile_capture_ids": profile_map[row["source_url"]]["capture_ids"], **site_collection(url)}
            website_evidence.append(item)
        for number, row in enumerate(registry, 1):
            candidates = [row["candidate_geoid"]] if row.get("candidate_geoid") in geoids else []
            if row.get("candidate_geoid") and not candidates:
                self.issues.append({"kind": "registry_candidate_not_in_baseline", "record": number, "candidate_geoid": row["candidate_geoid"]})
            raw_reference = self.reference("sources/official_courts/" + row["discovery_evidence_path"])
            website_evidence.append({"website_evidence_id": "website_" + value_hash(["cisa", row["domain"]])[:24], "evidence_family": "official_domain_registry_candidate",
                "url": row["official_url"], "registered_domain": row["domain"], "organization": row["organization"], "registry_state_code": row["usps"],
                "assigned_geoid": None, "candidate_geoids": candidates, "candidate_county_name": row["candidate_county_name"],
                "county_association_status": row["county_match_status"], "county_association_reason": row["county_match_method"],
                "site_authority_verified": False, "authority_classification": "official_government_domain_registration",
                "registration_verification_status": row["verification_status"], "entry_url_basis": "HTTPS root entry derived from the registered domain; not an observed page URL",
                "provenance_locator": "sources/official_courts/datasets/cisa_county_government_domains.json#record/" + str(number),
                "official_registry_url": row["discovered_from"], "registry_raw_path": raw_reference, **site_collection(row["official_url"])})
        website_evidence.sort(key=lambda row: row["website_evidence_id"])
        assert len({row["website_evidence_id"] for row in website_evidence}) == len(website_evidence)

        matched, ambiguous, reported_websites, candidate_websites = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
        for profile in profile_map.values():
            if profile["match_status"] == "matched":
                assert profile["census_geoid"] in geoids
                matched[profile["census_geoid"]].append(profile)
            elif profile["match_status"] == "ambiguous":
                assert profile["census_geoid"] is None
                for geoid in profile["candidate_geoids"]:
                    ambiguous[geoid].append(profile)
        for website in website_evidence:
            if website["assigned_geoid"]:
                assert website["assigned_geoid"] in geoids and website["evidence_family"] == "trellis_reported_website"
                reported_websites[website["assigned_geoid"]].append(website)
            for geoid in website["candidate_geoids"]:
                candidate_websites[geoid].append(website)

        counties = []
        for census_number, county in sorted(enumerate(census, 1), key=lambda pair: pair[1]["geoid"]):
            geoid = county["geoid"]
            profiles, candidate_profiles = matched.get(geoid, []), ambiguous.get(geoid, [])
            reports, candidates = reported_websites.get(geoid, []), candidate_websites.get(geoid, [])
            report_captures = sorted({reference["capture_id"] for website in reports for reference in website["saved_capture_refs"]})
            candidate_captures = sorted({reference["capture_id"] for website in candidates for reference in website["saved_capture_refs"]})
            counties.append({"geoid": geoid, "state_fips": county["state_fips"], "county_fips": county["county_fips"], "state": county["state"],
                "usps": county["usps"], "name": county["name"], "geography_vintage": county["geography_vintage"],
                "census_provenance_locator": "reports/geography/input_snapshots/census.json#record/" + str(census_number),
                "trellis_profile_status": "matched_saved_profile" if profiles else ("ambiguous_candidate_only" if candidate_profiles else "not_matched_in_saved_snapshot"),
                "trellis_profile_urls": sorted(profile["source_url"] for profile in profiles), "trellis_profile_ids": sorted(profile["profile_id"] for profile in profiles),
                "trellis_raw_paths": sorted({path for profile in profiles for path in profile["raw_paths"]}),
                "ambiguous_profile_urls": sorted(profile["source_url"] for profile in candidate_profiles),
                "ambiguous_profile_ids": sorted(profile["profile_id"] for profile in candidate_profiles),
                "website_evidence_status": "trellis_reported_website" if reports else ("registry_candidates_only" if candidates else "no_associated_website_evidence"),
                "reported_website_urls": sorted({website["url"] for website in reports if website["url"]}),
                "reported_website_evidence_ids": sorted(website["website_evidence_id"] for website in reports),
                "registry_candidate_urls": sorted({website["url"] for website in candidates}),
                "registry_candidate_evidence_ids": sorted(website["website_evidence_id"] for website in candidates),
                "reported_site_capture_ids": report_captures, "candidate_site_capture_ids": candidate_captures,
                "reported_site_nonempty_text_capture_ids": [capture_id for capture_id in report_captures if self.captures[capture_id]["trimmed_text_characters"]],
                "candidate_site_nonempty_text_capture_ids": [capture_id for capture_id in candidate_captures if self.captures[capture_id]["trimmed_text_characters"]],
                "reported_site_raw_paths": sorted({self.captures[capture_id]["raw_path"] for capture_id in report_captures}),
                "candidate_site_raw_paths": sorted({self.captures[capture_id]["raw_path"] for capture_id in candidate_captures}),
                "reported_site_text_paths": sorted({self.captures[capture_id]["text_path"] for capture_id in report_captures if self.captures[capture_id]["text_path"]}),
                "candidate_site_text_paths": sorted({self.captures[capture_id]["text_path"] for capture_id in candidate_captures if self.captures[capture_id]["text_path"]}),
                "site_authority_independently_verified": False})
        profiles = sorted(profile_map.values(), key=lambda row: row["source_url"])
        special = [profile_map[row["source_url"]] for row in special_source]
        ambiguities = [profile_map[row["source_url"]] for row in ambiguity_source]
        assert all(row["census_geoid"] is None for row in special + ambiguities)
        assert len(matched) == geography["matched_unique_census_geoids"]
        assert len(counties) - len(matched) == geography["missing_census_geographies_in_snapshot"]
        capture_rows = sorted(self.captures.values(), key=lambda row: row["capture_id"])
        offline_derivatives = []
        derivative_manifest = "corpus/county_entries_resume_20260913/offline_decoding/manifest.jsonl"
        if (self.root / derivative_manifest).is_file():
            self.reference(derivative_manifest)
            for row in (json.loads(line) for line in (self.root / derivative_manifest).read_text(encoding="utf-8").splitlines() if line.strip()):
                assert row["status"] == "extracted" and row["capture_kind"] == "document_text_derivative"
                assert any(capture["source_url"] == row["source_url"] and capture["raw_sha256"] == row["parent_raw_sha256"] for capture in capture_rows)
                for path_key, hash_key in (("parent_raw_path", "parent_raw_sha256"), ("parent_metadata_path", "parent_metadata_sha256"), ("decoded_path", "decoded_sha256"), ("text_path", "text_sha256")):
                    self.reference(row[path_key], row[hash_key])
                offline_derivatives.append({"manifest_path": derivative_manifest, "source_url": row["source_url"], "parent_raw_sha256": row["parent_raw_sha256"],
                    "text_path": row["text_path"], "text_sha256": row["text_sha256"], "text_bytes": row["text_bytes"],
                    "available_as_separate_local_text": True, "included_in_unified_fts_index": False})
        capture_ids = set(self.captures)
        website_ids = {row["website_evidence_id"] for row in website_evidence}
        for row in counties:
            assert set(row["reported_site_capture_ids"] + row["candidate_site_capture_ids"]) <= capture_ids
            assert set(row["reported_website_evidence_ids"] + row["registry_candidate_evidence_ids"]) <= website_ids
        unassigned = [row for row in website_evidence if not row["assigned_geoid"] and not row["candidate_geoids"]]
        states = [{"state": state, "county_equivalents": len(rows), "trellis_matched": sum(bool(row["trellis_profile_urls"]) for row in rows),
                   "ambiguous_candidate_only": sum(row["trellis_profile_status"] == "ambiguous_candidate_only" for row in rows),
                   "reported_website_evidence": sum(bool(row["reported_website_urls"]) for row in rows),
                   "registry_website_candidates": sum(bool(row["registry_candidate_urls"]) for row in rows)}
                  for state, rows in sorted(((state, [row for row in counties if row["state"] == state]) for state in {row["state"] for row in counties}))]
        for stem, rows in (("counties", counties), ("trellis_profiles", profiles), ("special_jurisdictions", special), ("ambiguities", ambiguities),
                           ("website_evidence", website_evidence), ("unassigned_website_evidence", unassigned), ("captures", capture_rows), ("state_summary", states)):
            self.table(stem, rows, list(counties[0]) if stem == "counties" else None)
        self.table("file_references", sorted(self.references.values(), key=lambda row: row["path"]))
        fingerprint = value_hash({"builder_version": VERSION, "inputs": self.inputs})
        generated_at = datetime.now(timezone.utc).isoformat()
        summary = {"builder_version": VERSION, "generated_at": generated_at, "build_fingerprint": fingerprint,
            "county_equivalent_rows": len(counties), "states_and_dc": len(states), "census_vintage": 2026,
            "trellis_profile_rows": len(profiles), "matched_counties": len(matched), "counties_without_clear_trellis_match": len(counties) - len(matched),
            "trellis_profile_status_counts": dict(Counter(row["trellis_profile_status"] for row in counties)),
            "special_jurisdiction_rows": len(special), "ambiguous_profile_rows": len(ambiguities),
            "website_evidence_rows": len(website_evidence), "trellis_reported_website_rows": len(websites_source), "cisa_registry_domain_rows": len(registry),
            "cisa_registry_candidate_rows": sum(bool(row.get("candidate_geoid")) for row in registry), "unassigned_website_evidence_rows": len(unassigned),
            "website_evidence_status_counts": dict(Counter(row["website_evidence_status"] for row in counties)),
            "counties_with_reported_site_captures": sum(bool(row["reported_site_capture_ids"]) for row in counties),
            "counties_with_candidate_site_captures": sum(bool(row["candidate_site_capture_ids"]) for row in counties),
            "saved_capture_rows": len(capture_rows), "capture_rows_by_collection": dict(Counter(row["collection"] for row in capture_rows)),
            "capture_content_availability_counts": dict(Counter(row["content_availability"] for row in capture_rows)),
            "county_site_snapshot_resource_status_counts": dict(Counter(row["status"] for row in resources)),
            "county_collection_snapshots": {collection: {"resources": len(snapshot["resources"]), "contexts": len(snapshot["contexts"]),
                "resource_contexts": len(snapshot["resource_contexts"])} for collection, snapshot in snapshots.items()},
            "offline_text_derivative_manifests": offline_derivatives,
            "file_references_checked": len(self.references), "file_reference_issues": len(self.issues),
            "county_baseline_complete_for_saved_50_states_and_dc": True, "full_court_case_corpus_complete": False,
            "official_website_county_authority_independently_verified": False, "network_requests": 0, "source_files_modified": False}
        self.json("summary.json", summary)
        self.json("provenance.json", {"generated_at": generated_at, "build_fingerprint": fingerprint, "inputs": self.inputs,
            "census_source": census_provenance, "county_snapshot_path": "delivery/focused_legal_corpus/counties/county_site_snapshot.json", "county_snapshot_paths": snapshot_paths,
            "method": "Reuse saved exact geography reconciliation; retain CISA name hints as candidates only; join captures through recorded seed contexts or exact saved source URLs. No new name matching or network calls."})
        self.json("validation.json", {"validated_at": generated_at, "build_fingerprint": fingerprint, "passed": not self.issues,
            "checks": {"county_rows": 3144, "unique_geoids": 3144, "state_fips_plus_county_fips_matches_geoid": True, "states_and_dc": len(states),
                "matched_profiles": len(matched), "special_rows_preserved_without_geoid": len(special), "ambiguous_rows_preserved_without_geoid": len(ambiguities),
                "all_capture_and_website_ids_resolve": True, "unique_file_references_hashed": len(self.references)}, "issues": self.issues})
        self.json("schema.json", {"format": "JSONL rows are canonical; CSV nested fields contain JSON arrays/objects.", "path_basis": "workspace-relative, never relative to this delivery subdirectory",
            "county_primary_key": "geoid", "county_keys": list(counties[0]), "county_string_identifiers": ["geoid", "state_fips", "county_fips", "usps"],
            "profile_primary_key": "profile_id", "website_primary_key": "website_evidence_id", "capture_primary_key": "capture_id",
            "candidate_policy": "registry_candidate_* and ambiguous_profile_* are unresolved candidates, never confirmed assignments",
            "file_reference_table": "file_references.jsonl contains existence, fresh SHA-256 and byte count for every referenced source artifact."})
        (self.output / "README.md").write_text(f"""# Focused county delivery

This offline package contains all **3,144 saved Census county-equivalent rows** across the 50 states and DC (saved vintage 2026). It is a geography and evidence lookup, not a claim that every county's court records or website has been collected.

`counties.csv` and `counties.jsonl` contain one row per GEOID, with state/name, saved Trellis profile status and links, reported website evidence, separately labeled registry candidates, and existing raw/text paths. Treat GEOID and FIPS fields as strings so leading zeros survive. Nested CSV values are JSON; JSONL preserves them directly. All source paths are relative to the workspace root, so retain this delivery within the workspace or preserve that directory layout when moving it.

The saved reconciliation supplies **{len(matched):,} clear Trellis matches**, **{len(ambiguities)} ambiguous profiles**, and **{len(special)} special jurisdictions**. The other **{len(counties)-len(matched):,}** Census geographies have no clear saved Trellis match; {sum(row['trellis_profile_status'] == 'ambiguous_candidate_only' for row in counties)} have an ambiguous candidate. Missing means not matched in this snapshot. It does not mean the county is absent from Trellis or the government.

`special_jurisdictions` preserves historical county court scopes and non-county jurisdictions separately. `ambiguities` preserves every conflicting profile in the current geography snapshot, including the earlier Florida Jackson/Washington and Sarasota/Manatee conflicts. Neither table assigns a Census GEOID.

`website_evidence` preserves {len(websites_source):,} Trellis Website-field observations and {len(registry):,} official CISA domain registrations. Trellis name matching does not verify site ownership. CISA registration verifies a government-domain registration; its saved county-name hints remain unreviewed candidates. Unassigned and ambiguous website evidence remains in `unassigned_website_evidence`. No candidate is forced into a confirmed county match.

`captures` references {len(capture_rows):,} existing saved captures without copying their contents. Website captures are associated through a recorded collector seed context or an exact source URL. Reviewed apex/www entries derived from robots redirects retain an explicit derived relation, original href, new seed URL, and source control/response hashes; they are not labeled as observed page hrefs. An off-host redirect remains explicit in the website's capture references and does not verify county authority. Pending, denied and failed collector states remain status counts, not saved legal text.

Separate offline text derivatives, when present, are listed in `summary.json` under `offline_text_derivative_manifests`. The [Crawford County decoding manifest](../../../corpus/county_entries_resume_20260913/offline_decoding/manifest.jsonl) links its recovered text outside the unified FTS index; original compressed capture bytes and extraction metadata remain preserved.

`state_summary`, `summary.json`, `schema.json`, `provenance.json`, `validation.json` and `file_references` describe counts, keys, inputs and validation. The county snapshot files preserve the compact resource/context rows used for this read-only join. The joined collections are {', '.join('`' + value + '`' for value in COUNTY_COLLECTIONS)}; numeric resource IDs are namespaced by collection, and shared-URL geography context IDs remain visible. Source captures, databases, queues and credentials are unchanged.

Build again with `python scripts/build_focused_counties.py`. Row ordering, IDs and joins are deterministic for the same saved inputs; timestamps identify the build and the input fingerprint distinguishes later snapshots. This build used no network or browser requests. Validation checked all 3,144 unique GEOIDs, FIPS consistency, the {len(special)}+{len(ambiguities)} unresolved rows, evidence ID joins, and {len(self.references):,} source-file references and hashes. File-reference issues: **{len(self.issues)}**.
""", encoding="utf-8")
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = Delivery(args.root, args.output or args.root / "delivery/focused_legal_corpus/counties").build()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["file_reference_issues"]))


if __name__ == "__main__":
    main()
