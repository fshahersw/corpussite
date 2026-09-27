import json

base = "C:/Users/firas/Downloads/returnedfiles/"
d = json.load(open(base+"01a01fb3-97f0-7680-9b66-ba37a4f515f4.json", encoding='utf-8'))
links = d.get('links')
print("links type:", type(links), "len:", len(links) if links else 0)
print(links[:10])

d2 = json.load(open(base+"01a0202a-9bd9-7124-b2f8-2c91f5e05748.json", encoding='utf-8'))
links2 = d2.get('links')
print("links2 sample:", links2[:5] if links2 else None, "len", len(links2) if links2 else 0)

# subfolder file with links?
d3 = json.load(open(base+"01a02884-8ede-77df-a7ba-9e5c825a61d8/www.alleghenycourts.us_.json", encoding='utf-8'))
print("subfolder keys:", list(d3.keys()))
print("has links?", 'links' in d3)

# check how many bare-uuid files have 'links' vs not, and how many search_results have query info anywhere e.g. top-level besides web
import glob, os
count_with_links = 0
count_without = 0
for fn in glob.glob(base+"*.json"):
    base_name = os.path.basename(fn)
    if '_map_urls' in base_name or '_search_results' in base_name:
        continue
    try:
        dd = json.load(open(fn, encoding='utf-8'))
    except Exception as e:
        continue
    if isinstance(dd, dict) and 'links' in dd and dd['links']:
        count_with_links += 1
    else:
        count_without += 1
print("bare uuid files with non-empty links:", count_with_links, "without:", count_without)
