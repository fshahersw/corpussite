# -*- coding: utf-8 -*-
import json, re, os

BASE = "C:/Users/firas/Downloads/SCRAPE/sources/county_filing_sources_20260919"
STATES_DIR = BASE + "/states"
os.makedirs(STATES_DIR, exist_ok=True)

def load_receipts(state):
    p = f"{BASE}/captures/{state}/receipts.jsonl"
    if not os.path.exists(p):
        return {}
    out = {}
    for line in open(p, encoding="utf-8"):
        r = json.loads(line)
        out[r["url"]] = r
    return out

def fname(state, url, receipts):
    r = receipts.get(url)
    if r and r.get("file"):
        return r["file"]
    return None

def write_jsonl(state, rows):
    with open(f"{STATES_DIR}/{state}.jsonl", "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

def write_summary(state, summary):
    with open(f"{STATES_DIR}/{state}.summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

# ============================================================
# IL
# ============================================================
il_r = load_receipts("IL")

il_circuits = {
    "Circuit Court of Cook County": ["Cook County"],
    "1st Judicial Circuit": ["Alexander County","Jackson County","Johnson County","Massac County","Pope County","Pulaski County","Saline County","Union County","Williamson County"],
    "2nd Judicial Circuit": ["Crawford County","Edwards County","Franklin County","Gallatin County","Hamilton County","Hardin County","Jefferson County","Lawrence County","Richland County","Wabash County","Wayne County","White County"],
    "3rd Judicial Circuit": ["Bond County","Madison County"],
    "4th Judicial Circuit": ["Christian County","Clay County","Clinton County","Effingham County","Fayette County","Jasper County","Marion County","Montgomery County","Shelby County"],
    "5th Judicial Circuit": ["Clark County","Coles County","Cumberland County","Edgar County","Vermilion County"],
    "6th Judicial Circuit": ["Champaign County","De Witt County","Douglas County","Macon County","Moultrie County","Piatt County"],
    "7th Judicial Circuit": ["Greene County","Jersey County","Macoupin County","Morgan County","Sangamon County","Scott County"],
    "8th Judicial Circuit": ["Adams County","Brown County","Calhoun County","Cass County","Mason County","Menard County","Pike County","Schuyler County"],
    "9th Judicial Circuit": ["Fulton County","Hancock County","Henderson County","Knox County","McDonough County","Warren County"],
    "10th Judicial Circuit": ["Marshall County","Peoria County","Putnam County","Stark County","Tazewell County"],
    "11th Judicial Circuit": ["Ford County","Livingston County","Logan County","McLean County","Woodford County"],
    "12th Judicial Circuit": ["Will County"],
    "13th Judicial Circuit": ["Bureau County","Grundy County","LaSalle County"],
    "14th Judicial Circuit": ["Henry County","Mercer County","Rock Island County","Whiteside County"],
    "15th Judicial Circuit": ["Carroll County","Jo Daviess County","Lee County","Ogle County","Stephenson County"],
    "16th Judicial Circuit": ["Kane County"],
    "17th Judicial Circuit": ["Boone County","Winnebago County"],
    "18th Judicial Circuit": ["DuPage County"],
    "19th Judicial Circuit": ["Lake County"],
    "20th Judicial Circuit": ["St. Clair County"],
    "21st Judicial Circuit": ["Iroquois County","Kankakee County"],
    "22nd Judicial Circuit": ["McHenry County"],
    "23rd Judicial Circuit": ["DeKalb County","Kendall County"],
    "24th Judicial Circuit": ["Monroe County","Perry County","Randolph County","Washington County"],
}

IL_CLERKS_URL = "https://www.illinoiscourts.gov/circuit-court/circuit-court-clerks/illinois-circuit-court-clerks-by-district-and-circuit/"
IL_RULES_URL = "https://www.illinoiscourts.gov/rules-law/supreme-court-rules/"
IL_FORMS_URL = "https://www.illinoiscourts.gov/documents-and-forms/approved-forms/"
IL_EFILE_URL = "https://efile.illinoiscourts.gov/"
IL_17TH_URL = "https://17thcircuit.illinoiscourts.gov/for-attorneys/rules-orders"
IL_COOK_URL = "https://www.cookcountycourtil.gov/"

il_rows = []
for unit_name, counties in il_circuits.items():
    unit_type = "statewide" if False else ("county" if unit_name == "Circuit Court of Cook County" else "judicial circuit")
    il_rows.append({
        "state": "IL", "scope": "unit", "unit_type": unit_type, "unit_name": unit_name,
        "counties": counties, "kind": "clerk_or_court_directory",
        "title": "Illinois Circuit Court Clerks by Circuit",
        "url": IL_CLERKS_URL, "publisher": "Administrative Office of the Illinois Courts",
        "evidence": {"capture_file": fname("IL", IL_CLERKS_URL, il_r), "found_as": "page_itself", "anchor_text": None},
        "county_mapping_basis": "Page lists each judicial circuit as a heading followed by the clerk's office (name, address, phone) for every county in that circuit; used verbatim from the captured page.",
        "notes": None,
    })

# statewide rows (apply to all IL counties)
all_il_counties = [c for v in il_circuits.values() for c in v]
for kind, url, title in [
    ("statewide_rules", IL_RULES_URL, "Illinois Supreme Court Rules"),
    ("court_forms", IL_FORMS_URL, "Approved Statewide Standardized Forms"),
    ("efiling", IL_EFILE_URL, "eFileIL | Court E-Filing Solution for Illinois"),
]:
    il_rows.append({
        "state": "IL", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
        "counties": "*", "kind": kind, "title": title, "url": url,
        "publisher": "Administrative Office of the Illinois Courts",
        "evidence": {"capture_file": fname("IL", url, il_r), "found_as": "page_itself", "anchor_text": None},
        "county_mapping_basis": "statewide", "notes": None,
    })

# local rules - partial (17th circuit, Cook County directory)
il_rows.append({
    "state": "IL", "scope": "unit", "unit_type": "judicial circuit", "unit_name": "17th Judicial Circuit",
    "counties": ["Boone County", "Winnebago County"], "kind": "local_rules",
    "title": "Rules/Orders (17th Judicial Circuit Court of Illinois)",
    "url": IL_17TH_URL, "publisher": "17th Judicial Circuit Court of Illinois",
    "evidence": {"capture_file": fname("IL", IL_17TH_URL, il_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "17th Judicial Circuit is confirmed to consist of Boone and Winnebago Counties per the Illinois Circuit Court Clerks by Circuit page.",
    "notes": None,
})
il_rows.append({
    "state": "IL", "scope": "unit", "unit_type": "county", "unit_name": "Circuit Court of Cook County",
    "counties": ["Cook County"], "kind": "clerk_or_court_directory",
    "title": "Circuit Court of Cook County - Official Website",
    "url": IL_COOK_URL, "publisher": "Circuit Court of Cook County",
    "evidence": {"capture_file": fname("IL", IL_COOK_URL, il_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide list confirms Cook County has its own separate circuit court (not a numbered circuit).",
    "notes": None,
})

write_jsonl("IL", il_rows)

il_summary = {
    "state": "IL",
    "trial_court_structure": "Illinois has 24 numbered judicial circuits plus the separate Circuit Court of Cook County; single-county circuits are Cook, Kane, Will, DuPage, Lake and McHenry, while the other circuits comprise 2-12 counties each.",
    "has_local_rules": True,
    "counties_total": 102,
    "counties_with_local_rules_row": 2,
    "counties_with_any_row": 102,
    "captures_used": 9,
    "gaps": [
        "No single statewide hub links every circuit's local rules pages; each of the 24 circuits (and Cook County) maintains its own separate website. Only the 17th Circuit (Boone, Winnebago) local rules index and the Cook County Circuit Court homepage were captured within budget; local_rules rows for the other 22 circuits are not included (would require crawling ~23 additional individual circuit sites).",
        "No official statewide filing-fee schedule page was found; filing fees are set/collected at the circuit clerk level and are not compiled centrally, so no fee_schedule rows are included.",
    ],
    "sources_consulted": [IL_CLERKS_URL, IL_RULES_URL, IL_FORMS_URL, IL_EFILE_URL, IL_17TH_URL, IL_COOK_URL,
                            "https://www.illinoiscourts.gov/courts/circuit-court/", "https://www.illinoiscourts.gov/courts-directory/",
                            "https://efile.illinoiscourts.gov/self-represented-filers-page/"],
}
write_summary("IL", il_summary)
print("IL done", len(il_rows))

# ============================================================
# PA
# ============================================================
pa_r = load_receipts("PA")

pa_counties_raw = ["Adams","Allegheny","Armstrong","Beaver","Bedford","Berks","Blair","Bradford","Bucks","Butler",
"Cambria","Cameron","Carbon","Centre","Chester","Clarion","Clearfield","Clinton","Columbia","Crawford","Cumberland",
"Dauphin","Delaware","Elk","Erie","Fayette","Forest","Franklin","Fulton","Greene","Huntingdon","Indiana","Jefferson",
"Juniata","Lackawanna","Lancaster","Lawrence","Lebanon","Lehigh","Luzerne","Lycoming","McKean","Mercer","Mifflin",
"Monroe","Montgomery","Montour","Northampton","Northumberland","Perry","Philadelphia","Pike","Potter","Schuylkill",
"Snyder","Somerset","Sullivan","Susquehanna","Tioga","Union","Venango","Warren","Washington","Wayne","Westmoreland",
"Wyoming","York"]
pa_counties = [c + " County" for c in pa_counties_raw]  # Philadelphia is coextensive city-county but counties.jsonl uses "Philadelphia County"

PA_PROTHO_URL = "https://www.pacourts.us/courts/courts-of-common-pleas/prothonotaries"
PA_CLERKS_URL = "https://www.pacourts.us/courts/courts-of-common-pleas/clerks-of-courts"
PA_COMMONPLEAS_URL = "https://www.pacourts.us/courts/courts-of-common-pleas"
PA_FORMS_URL = "https://www.pacourts.us/forms"
PA_EFILE_URL = "https://ujsportal.pacourts.us/PACFile/Overview"
PA_SELFHELP_URL = "https://www.pacourts.us/learn/representing-yourself"

pa_rows = []
pa_rows.append({
    "state": "PA", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "clerk_or_court_directory",
    "title": "Prothonotaries",
    "url": PA_PROTHO_URL, "publisher": "Unified Judicial System of Pennsylvania",
    "evidence": {"capture_file": fname("PA", PA_PROTHO_URL, pa_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "Page contains a 'County Prothonotaries' table listing an entry (name, address, phone) for every one of Pennsylvania's 67 counties.",
    "notes": None,
})
pa_rows.append({
    "state": "PA", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "court_forms",
    "title": "Forms", "url": PA_FORMS_URL, "publisher": "Unified Judicial System of Pennsylvania",
    "evidence": {"capture_file": fname("PA", PA_FORMS_URL, pa_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide", "notes": None,
})
pa_rows.append({
    "state": "PA", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "efiling",
    "title": "PACFile", "url": PA_EFILE_URL, "publisher": "Unified Judicial System of Pennsylvania",
    "evidence": {"capture_file": fname("PA", PA_EFILE_URL, pa_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "PACFile is the statewide UJS e-filing portal for appellate/Commonwealth Court filings; many Courts of Common Pleas use their own county e-filing systems not centrally captured here (see gaps).",
})
pa_rows.append({
    "state": "PA", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "no_local_rules_statement",
    "title": "Courts of Common Pleas",
    "url": PA_COMMONPLEAS_URL, "publisher": "Unified Judicial System of Pennsylvania",
    "evidence": {"capture_file": fname("PA", PA_COMMONPLEAS_URL, pa_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "Page states verbatim: 'Local rules of Pennsylvania courts are maintained individually by each county,' linking to the Individual County Courts page. That page's county selector is a client-side dropdown with no per-county URLs present in the captured HTML/links, so per-county local_rules URLs could not be captured within the evidence rules (see gaps).",
})

write_jsonl("PA", pa_rows)

pa_summary = {
    "state": "PA",
    "trial_court_structure": "The Courts of Common Pleas are organized into 60 judicial districts covering Pennsylvania's 67 counties and are the trial courts of Pennsylvania.",
    "has_local_rules": True,
    "counties_total": 67,
    "counties_with_local_rules_row": 0,
    "counties_with_any_row": 67,
    "captures_used": 7,
    "gaps": [
        "The Pennsylvania Code/Bulletin (pacodeandbulletin.gov), which compiles Title 231 statewide civil procedure rules and Title 255 local court rules, is robots-disallowed for automated capture, so no statewide_rules row and no per-county Title 255 local_rules rows could be captured.",
        "pacourts.us/courts/courts-of-common-pleas/individual-county-courts uses a client-side county-select dropdown; no per-county page URLs (e.g. for Butler County) appear in the captured page's static links, so local_rules rows for individual counties could not be built without inventing URLs.",
        "No single statewide e-filing system covers all Courts of Common Pleas civil filings; most counties operate independent e-filing systems (e.g. individual county Prothonotary portals) not linked from a single captured hub.",
        "No statewide filing-fee schedule page was found; Pennsylvania prothonotary filing fees are set per county.",
    ],
    "sources_consulted": [PA_PROTHO_URL, PA_CLERKS_URL, PA_COMMONPLEAS_URL, PA_FORMS_URL, PA_EFILE_URL, PA_SELFHELP_URL,
                            "https://www.pacourts.us/courts/courts-of-common-pleas/individual-county-courts"],
}
write_summary("PA", pa_summary)
print("PA done", len(pa_rows))

# ============================================================
# ME
# ============================================================
me_r = load_receipts("ME")

me_counties_raw = ["Androscoggin","Aroostook","Cumberland","Franklin","Hancock","Kennebec","Knox","Lincoln","Oxford",
"Penobscot","Piscataquis","Sagadahoc","Somerset","Waldo","Washington","York"]
me_courthouse_links = {
    "Androscoggin": "https://www.courts.maine.gov/courts/superior/androscoggin-sc.html",
    "Aroostook": "https://www.courts.maine.gov/courts/superior/aroostook-caribou-sc.html",
    "Cumberland": "https://www.courts.maine.gov/courts/superior/cumberland-sc.html",
    "Franklin": "https://www.courts.maine.gov/courts/superior/franklin-sc.html",
    "Hancock": "https://www.courts.maine.gov/courts/superior/hancock-sc.html",
    "Kennebec": "https://www.courts.maine.gov/courts/superior/kennebec-sc.html",
    "Knox": "https://www.courts.maine.gov/courts/superior/knox-sc.html",
    "Lincoln": "https://www.courts.maine.gov/courts/superior/lincoln-sc.html",
    "Oxford": "https://www.courts.maine.gov/courts/superior/oxford-sc.html",
    "Penobscot": "https://www.courts.maine.gov/courts/superior/penobscot-sc.html",
    "Piscataquis": "https://www.courts.maine.gov/courts/superior/piscataquis-sc.html",
    "Sagadahoc": "https://www.courts.maine.gov/courts/superior/sagadahoc-sc.html",
    "Somerset": "https://www.courts.maine.gov/courts/superior/somerset-sc.html",
    "Waldo": "https://www.courts.maine.gov/courts/superior/waldo-sc.html",
    "Washington": "https://www.courts.maine.gov/courts/superior/washington-sc.html",
    "York": "https://www.courts.maine.gov/courts/superior/york-sc.html",
}
ME_SUPERIOR_URL = "https://www.courts.maine.gov/courts/superior/index.html"
ME_RULES_URL = "https://www.courts.maine.gov/rules/index.html"
ME_FORMS_URL = "https://www.courts.maine.gov/forms/index.html"
ME_FEES_URL = "https://www.courts.maine.gov/forms/fees.html"
ME_ECOURTS_URL = "https://www.courts.maine.gov/ecourts/index.html"
ME_EFILE_URL = "https://www.courts.maine.gov/ecourts/efile.html"
ME_FINDCOURT_URL = "https://www.courts.maine.gov/courts/find-a-court.html"

me_rows = []
me_rows.append({
    "state": "ME", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "clerk_or_court_directory",
    "title": "Superior Court",
    "url": ME_SUPERIOR_URL, "publisher": "State of Maine Judicial Branch",
    "evidence": {"capture_file": fname("ME", ME_SUPERIOR_URL, me_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "Page states 'Courts are located in each of Maine's eight judicial regions' and provides a Directory table with one row per county (Aroostook has two courthouse locations, Caribou and Houlton) covering all 16 counties.",
    "notes": None,
})
for county, url in me_courthouse_links.items():
    me_rows.append({
        "state": "ME", "scope": "county", "unit_type": "county", "unit_name": None,
        "counties": [county + " County"], "kind": "clerk_or_court_directory",
        "title": f"{county} County Superior Court",
        "url": url, "publisher": "State of Maine Judicial Branch",
        "evidence": {"capture_file": fname("ME", ME_SUPERIOR_URL, me_r), "found_as": "link_on_page", "anchor_text": county if county != "Aroostook" else "Aroostook (Caribou)"},
        "county_mapping_basis": "Row for this county in the Superior Court Directory table.",
        "notes": None,
    })
for kind, url, title in [
    ("statewide_rules", ME_RULES_URL, "Court Rules"),
    ("court_forms", ME_FORMS_URL, "Court Forms"),
    ("fee_schedule", ME_FEES_URL, "Court Fees"),
    ("efiling", ME_EFILE_URL, "About eFileMaine"),
]:
    me_rows.append({
        "state": "ME", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
        "counties": "*", "kind": kind, "title": title, "url": url,
        "publisher": "State of Maine Judicial Branch",
        "evidence": {"capture_file": fname("ME", url, me_r), "found_as": "page_itself", "anchor_text": None},
        "county_mapping_basis": "statewide", "notes": None,
    })
me_rows.append({
    "state": "ME", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "no_local_rules_statement",
    "title": "Court Rules",
    "url": ME_RULES_URL, "publisher": "State of Maine Judicial Branch",
    "evidence": {"capture_file": fname("ME", ME_RULES_URL, me_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "The Court Rules index lists only statewide rule sets (Civil, Criminal, Evidence, Appellate, Probate, Family Division, Electronic Court Systems, Small Claims, etc.); no county-specific local rules are listed, consistent with Maine's unified statewide trial court system.",
})

write_jsonl("ME", me_rows)

me_summary = {
    "state": "ME",
    "trial_court_structure": "Maine's Superior Court is the trial court of general jurisdiction with one court location in each of Maine's 16 counties (grouped into 8 judicial regions), and the District Court is a unified statewide court also organized by location; jury trials are only available in Superior Court.",
    "has_local_rules": False,
    "counties_total": 16,
    "counties_with_local_rules_row": 0,
    "counties_with_any_row": 16,
    "captures_used": 8,
    "gaps": [
        "Maine's trial courts operate under statewide rules (Maine Rules of Civil Procedure, etc.); the Court Rules index page shows no county-specific local rules, so no local_rules rows are included (see no_local_rules_statement row).",
        "eFileMaine (Guide and File / EFS) is being rolled out court-by-court; the captured eCourts/eFile pages describe the statewide program but do not list per-county rollout status in a table, so a single statewide efiling row is used for all counties rather than per-county rows.",
    ],
    "sources_consulted": [ME_SUPERIOR_URL, ME_FINDCOURT_URL, ME_RULES_URL, ME_FORMS_URL, ME_FEES_URL, ME_ECOURTS_URL, ME_EFILE_URL,
                            "https://www.courts.maine.gov/adminorders/index.html"],
}
write_summary("ME", me_summary)
print("ME done", len(me_rows))

# ============================================================
# NE
# ============================================================
ne_r = load_receipts("NE")

NE_DISTRICTINFO_URL = "https://nebraskajudicial.gov/directories/district-information"
NE_DISTRULES_URL = "https://nebraskajudicial.gov/external-court-rules/district-court-local-rules"
NE_COUNTYRULES_URL = "https://nebraskajudicial.gov/external-court-rules/county-court-local-rules"
NE_EFILE_URL = "https://nebraskajudicial.gov/e-services/efiling"
NE_FEES_URL = "https://nebraskajudicial.gov/rules/administrative-policies-schedules/filing-fees-and-court-costs"
NE_FORMS1_URL = "https://nebraskajudicial.gov/self-help/general-court-forms"
NE_FORMS2_URL = "https://nebraskajudicial.gov/forms"
NE_SCRULES_URL = "https://nebraskajudicial.gov/supreme-court-rules"

ne_rows = []
for kind, url, title in [
    ("statewide_rules", NE_SCRULES_URL, "Supreme Court Rules"),
    ("court_forms", NE_FORMS2_URL, "Master Forms List"),
    ("court_forms", NE_FORMS1_URL, "General Court Forms"),
    ("efiling", NE_EFILE_URL, "eFiling"),
    ("fee_schedule", NE_FEES_URL, "Filing Fees and Court Costs"),
]:
    ne_rows.append({
        "state": "NE", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
        "counties": "*", "kind": kind, "title": title, "url": url,
        "publisher": "Nebraska Judicial Branch",
        "evidence": {"capture_file": fname("NE", url, ne_r), "found_as": "page_itself", "anchor_text": None},
        "county_mapping_basis": "statewide", "notes": None,
    })

ne_rows.append({
    "state": "NE", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "local_rules", "title": "District Court Local Rules",
    "url": NE_DISTRULES_URL, "publisher": "Nebraska Judicial Branch",
    "evidence": {"capture_file": fname("NE", NE_DISTRULES_URL, ne_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "This hub links the 12 district-specific local rules pages (District 1 through District 12), plus the statewide Uniform District Court Rules of Practice and Procedure. Per-district county composition is shown only as an image map (ne-district-map-2018) on the District Information page, not as machine-readable text, so unit-level rows mapping specific counties to each of the 12 districts are not included here (see gaps) rather than guessing county lists.",
})
ne_rows.append({
    "state": "NE", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "local_rules", "title": "County Court Local Rules",
    "url": NE_COUNTYRULES_URL, "publisher": "Nebraska Judicial Branch",
    "evidence": {"capture_file": fname("NE", NE_COUNTYRULES_URL, ne_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "Hub of County Court local rules; same county-mapping limitation as District Court Local Rules above.",
})
ne_rows.append({
    "state": "NE", "scope": "statewide", "unit_type": "statewide", "unit_name": None,
    "counties": "*", "kind": "clerk_or_court_directory", "title": "District Information",
    "url": NE_DISTRICTINFO_URL, "publisher": "Nebraska Judicial Branch",
    "evidence": {"capture_file": fname("NE", NE_DISTRICTINFO_URL, ne_r), "found_as": "page_itself", "anchor_text": None},
    "county_mapping_basis": "statewide",
    "notes": "Page confirms Nebraska's 12 judicial districts cover all 93 counties (example rows shown: Fillmore County = County Court #10 / District Court #1; Otoe County = County Court #2 / District Court #1) but the full county list is presented only as a map image, not extractable text.",
})

write_jsonl("NE", ne_rows)

ne_summary = {
    "state": "NE",
    "trial_court_structure": "Nebraska trial courts consist of the District Courts (12 judicial districts covering all 93 counties) and separately-numbered County Courts; district and county court boundaries differ in Districts 1, 2 and 10.",
    "has_local_rules": True,
    "counties_total": 93,
    "counties_with_local_rules_row": 93,
    "counties_with_any_row": 93,
    "captures_used": 8,
    "gaps": [
        "The official county-to-district map (ne-district-map-2018) on the District Information page is an image, not text, so this deliverable does not include unit-level local_rules rows naming which specific counties fall in each of the 12 judicial districts; only statewide-scoped rows citing the two rules hubs (which link to all 12 district/ all county pages) are included.",
        "No single official page enumerating all 93 counties by district in text form was captured within budget; a district-by-district capture (12 additional pages) would be needed to build precise unit-level county_mapping_basis rows.",
    ],
    "sources_consulted": [NE_DISTRICTINFO_URL, NE_DISTRULES_URL, NE_COUNTYRULES_URL, NE_EFILE_URL, NE_FEES_URL, NE_FORMS1_URL, NE_FORMS2_URL, NE_SCRULES_URL],
}
write_summary("NE", ne_summary)
print("NE done", len(ne_rows))

# ============================================================
# MI
# ============================================================
mi_r = load_receipts("MI")
write_jsonl("MI", [])
mi_summary = {
    "state": "MI",
    "trial_court_structure": "Michigan's trial courts (Circuit Courts for felonies/civil claims over $25,000, District Courts for misdemeanors/civil claims up to $25,000, and Probate Courts) are established under the Revised Judicature Act of 1961 (MCL 600) and administered statewide by the State Court Administrative Office via courts.michigan.gov.",
    "has_local_rules": "varies",
    "counties_total": 83,
    "counties_with_local_rules_row": 0,
    "counties_with_any_row": 0,
    "captures_used": 2,
    "gaps": [
        "courts.michigan.gov (the Michigan Judicial Branch's entire domain, which hosts Local Court Rules, Michigan Court Rules, the Trial Court Directory, MiFILE e-filing information, and SCAO-approved forms) publishes a robots.txt with 'Disallow: /' for all user agents, blocking every page on that domain from compliant automated capture. All 7 attempted URLs under courts.michigan.gov returned robots_disallowed and were not retried per instructions.",
        "No compliant alternate official hub was found: the Michigan Legislature site (legislature.mi.gov) hosts the underlying statutes (e.g. MCL 600.8121, captured) but these are drafted city/township-by-township for District Courts rather than as a clean circuit-to-county table, and do not cover local rules, forms, e-filing, fee schedules, or a clerk directory.",
        "Because the state's own judiciary hub could not be captured, and the task instructs not to crawl individual county court websites unless linked from a captured state hub, no county-level rows (local_rules, statewide_rules, court_forms, efiling, fee_schedule, or clerk_or_court_directory) are included for Michigan in this deliverable. This is a hard external blocker (robots.txt), not a research gap.",
    ],
    "sources_consulted": [
        "https://www.courts.michigan.gov/rules-administrative-orders-and-jury-instructions/current-rules-and-jury-instructions/local-court-rules/ (robots_disallowed)",
        "https://www.courts.michigan.gov/rules-administrative-orders-and-jury-instructions/current-rules-and-jury-instructions/michigan-court-rules/ (robots_disallowed)",
        "https://www.courts.michigan.gov/trial-court-directory/ (robots_disallowed)",
        "https://www.courts.michigan.gov/mifile-systems/mifile-filers-in-the-trial-courts/ (robots_disallowed)",
        "https://www.courts.michigan.gov/SCAO-forms/circuit-court-forms/ (robots_disallowed)",
        "https://www.courts.michigan.gov/SCAO-forms/ (robots_disallowed)",
        "https://www.courts.michigan.gov/courts/trial-courts/ (robots_disallowed)",
        "https://www.legislature.mi.gov/Laws/MCL?objectName=mcl-600-8121 (captured, not used as a row source)",
        "https://www.legislature.mi.gov/Laws/MCL?objectName=MCL-600-8101 (captured, not used as a row source)",
    ],
}
write_summary("MI", mi_summary)
print("MI done (gap-documented, 0 rows)")

