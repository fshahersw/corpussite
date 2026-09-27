import json, collections, glob, os

base = "C:/Users/firas/Downloads/returnedfiles/"

# map_urls: host diversity
for fn in ["01a01d4c-9552-721e-a2fe-0c87bc5a4e3b_map_urls.json", "01a02010-09a3-7125-93de-7f8d04409021_map_urls.json"]:
    d = json.load(open(base+fn, encoding='utf-8'))
    hosts = collections.Counter()
    import urllib.parse
    for item in d:
        u = item.get('url','')
        h = urllib.parse.urlsplit(u).hostname
        hosts[h]+=1
    print(fn, "n=",len(d), "top hosts:", hosts.most_common(5))

# search_results: check keys across several files
for fn in ["01a01d1c-5626-76c9-a2bc-1571cd71b94b_search_results.json","01a01fad-cd6a-75a5-acaf-4b184d7d9667_search_results.json","01a02048-e40b-72ca-93d0-00da8a1b9322_search_results.json"]:
    d = json.load(open(base+fn, encoding='utf-8'))
    print(fn, "keys:", list(d.keys()))
    if 'web' in d and d['web']:
        print("  sample item keys:", list(d['web'][0].keys()))

# bare uuid.json: check metadata across a few
for fn in ["01a01d14-f636-72b5-bfc0-2033f212d9a1.json","01a01fb3-97f0-7680-9b66-ba37a4f515f4.json","01a0202a-9bd9-7124-b2f8-2c91f5e05748.json"]:
    d = json.load(open(base+fn, encoding='utf-8'))
    print(fn, "keys:", list(d.keys()))
    md = d.get('metadata',{})
    print("  metadata keys:", list(md.keys()))
    print("  sourceURL:", md.get('sourceURL'), "url:", md.get('url'), "title:", md.get('title'))

# duplicate "(1)" files check content equality via sha256
import hashlib
def sha(fn):
    h=hashlib.sha256()
    with open(fn,'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

pairs = [
  ("01a02092-9517-75dd-a433-f2d575076907_map_urls (1).json","01a02092-9517-75dd-a433-f2d575076907_map_urls.json"),
  ("01a02011-2da0-76ca-a464-c367aa30ab37_search_results (1).json","01a02011-2da0-76ca-a464-c367aa30ab37_search_results.json"),
]
for a,b in pairs:
    print(a, "==", b, sha(base+a)==sha(base+b))
