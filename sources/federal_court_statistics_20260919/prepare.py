"""Freeze the exact URL list (seeds.jsonl) for the two uscourts.gov packets.

Offline. Every URL is copied from the scout listing
reports/corpus_upgrade_20260919/understand/packets/federal_stats_urls.jsonl;
nothing is constructed by pattern.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCOUT = ROOT / "reports/corpus_upgrade_20260919/understand/packets/federal_stats_urls.jsonl"
HOST = "www.uscourts.gov"

CJRA_WANTED = {"CJRA 1", "CJRA 2", "CJRA 3", "CJRA 4", "CJRA 7", "CJRA 8", "CJRA 9", "CJRA Appendices"}
FCMS_DISTRICT = {"fcms_na_distprofile0630.2026.xlsx", "fcms_na_distcomparison0630.2026.xlsx"}
METHODOLOGY = {"https://www.uscourts.gov/file/27866/download", "https://www.uscourts.gov/media/25754"}


def select(rows):
    a, b = [], []
    for row in rows:
        if row.get("host") != HOST:
            continue
        name = row["url"].rsplit("/", 1)[-1]
        packet = row.get("packet")
        if packet == "P1_uscourts_tables_xlsx" and row.get("table_id") != "S-22":
            a.append(row)
        elif packet == "P2_uscourts_fcms_cjra":
            if name in FCMS_DISTRICT or row["url"] in METHODOLOGY:
                a.append(row)
            elif row.get("table_id") in CJRA_WANTED:
                b.append(row)
        elif packet == "P5_judgeships_vacancies" and not name.startswith("archive-"):
            a.append(row)
    return a, b


def main():
    payload = SCOUT.read_bytes()
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    a, b = select(rows)
    seen, seeds = set(), []
    for label, chosen in (("A", a), ("B", b)):
        for index, row in enumerate(chosen, 1):
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            seeds.append({
                "seed_id": f"{label}{index:03d}", "packet": label, "url": row["url"],
                "listed_title": row.get("title"), "listed_table_id": row.get("table_id"),
                "listed_publication": row.get("publication"), "listed_period_label": row.get("period_label"),
                "listed_format": row.get("format"), "listed_size_label": row.get("size_label"),
                "category": row.get("category"), "source_page": row.get("parent_page"),
                "scout_priority": row.get("priority"), "min_delay_s": 3,
            })
    assert len([s for s in seeds if s["packet"] == "A"]) <= 85
    assert len([s for s in seeds if s["packet"] == "B"]) <= 100
    out = HERE / "seeds.jsonl"
    out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds), encoding="utf-8")
    (HERE / "seeds_receipt.json").write_text(json.dumps({
        "scout_list": str(SCOUT.relative_to(ROOT)).replace("\\", "/"),
        "scout_list_sha256": hashlib.sha256(payload).hexdigest(),
        "seeds_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "packet_A": len([s for s in seeds if s["packet"] == "A"]),
        "packet_B": len([s for s in seeds if s["packet"] == "B"]),
        "excluded": "S-22 (judicial complaints), appellate FCMS files, appellate profile explanation, CJRA 10/11, vacancy archive page, JPML and FJC hosts (other packets, not granted to this task)",
    }, indent=1), encoding="utf-8")
    print(len(seeds), "seeds")


if __name__ == "__main__":
    main()
