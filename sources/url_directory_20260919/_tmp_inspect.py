import json, sys

def show(path, n=3):
    print("=====", path, "=====")
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    print("top type:", type(d))
    if isinstance(d, list):
        print("len:", len(d))
        for item in d[:n]:
            print(repr(item)[:400])
    elif isinstance(d, dict):
        print("keys:", list(d.keys()))
        for k, v in d.items():
            if isinstance(v, list):
                print(k, "-> list len", len(v), "sample:", repr(v[:2])[:500])
            elif isinstance(v, dict):
                print(k, "-> dict keys", list(v.keys()))
            else:
                print(k, "->", repr(v)[:200])

base = "C:/Users/firas/Downloads/returnedfiles/"
show(base + "01a01d4c-9552-721e-a2fe-0c87bc5a4e3b_map_urls.json")
show(base + "01a01d1b-50d6-73c0-94be-272bb1ee51df_search_results.json")
show(base + "01a01d14-e1a9-7249-a354-abcde7e560d7.json")
show(base + "01a02884-8ede-77df-a7ba-9e5c825a61d8/www.alleghenycourts.us_.json")
