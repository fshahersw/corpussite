import json, glob, os, collections

base = "C:/Users/firas/Downloads/returnedfiles/"
for d in ["01a02884-8ede-77df-a7ba-9e5c825a61d8","01a02886-aa16-72b7-b441-bf5b552b0e90"]:
    keyc = collections.Counter()
    n=0
    with_links=0
    for fn in glob.glob(base+d+"/*.json"):
        n+=1
        try:
            dd = json.load(open(fn, encoding='utf-8'))
        except Exception as e:
            print("ERR", fn, e); continue
        keyc.update(dd.keys())
        if dd.get('links'):
            with_links+=1
    print(d, "n=",n, "keys:", keyc, "with_links:", with_links)

# check search_results files for any top-level query-ish fields beyond 'web' across all files
qkeys=collections.Counter()
for fn in glob.glob(base+"*_search_results*.json"):
    dd = json.load(open(fn, encoding='utf-8'))
    qkeys.update(dd.keys())
print("search_results top-level keys across all:", qkeys)

# check metadata.title missing rate & statusCode distribution for bare uuid files, and file size range for streaming decision
sizes=[]
for fn in glob.glob(base+"*.json"):
    b = os.path.basename(fn)
    if '_map_urls' in b or '_search_results' in b: continue
    sizes.append(os.path.getsize(fn))
print("bare uuid file sizes: min", min(sizes), "max", max(sizes), "n", len(sizes))

sizes2=[os.path.getsize(fn) for fn in glob.glob(base+"*_map_urls*.json")]
print("map_urls sizes: min", min(sizes2), "max", max(sizes2))
sizes3=[os.path.getsize(fn) for fn in glob.glob(base+"*_search_results*.json")]
print("search_results sizes: min", min(sizes3), "max", max(sizes3))
