import json

paths = [
    r"C:/Users/firas/Downloads/returnedfiles/ARKcodelaw/01a02b24-23b4-778b-a19f-05adcfbc4a2d/codes.findlaw.com_al_title-13a-criminal-code_al-code-sect-13a-6-2_.json",
    r"C:/Users/firas/Downloads/returnedfiles/MO-codelaw/01a02aff-8480-74a9-9567-af30b93c106a/mca.legmt.gov_bills_mca_help.html.json",
    r"C:/Users/firas/Downloads/returnedfiles/NV-codeslaw/01a02af9-bc5f-7032-bb13-b604450433bd/www.leg.state.nv.us_.json",
    r"C:/Users/firas/Downloads/returnedfiles/OKcodelaw/01a02b0d-8ddf-7449-93ed-92e2dee9d9e5/www.oscn.net_dockets_GetCaseInformation.aspx_db.json",
    r"C:/Users/firas/Downloads/returnedfiles/PA outputs/01a0287c-78c2-7106-a9f8-fb777bd1d750/courts.phila.gov_.json",
    r"C:/Users/firas/Downloads/returnedfiles/More PA outputs/01a0287e-3eab-70e5-8881-a6b8a64ab462/www.pacourts.us_.json",
    r"C:/Users/firas/Downloads/returnedfiles/dccourtsoutputs/01a0289c-a605-70b8-980d-561b1c720906/www.dccourts.gov_.json",
    r"C:/Users/firas/Downloads/returnedfiles/uscourtswidecrawl/01a02a3f-9e63-7248-b636-45668815a0e3/www.justice.gov_atr_case-document_information-609.json",
]
for p in paths:
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print(p, 'ERR', e)
        continue
    print('====', p)
    if isinstance(d, dict):
        print('keys:', list(d.keys())[:20])
        meta = d.get('metadata') or {}
        if isinstance(meta, dict):
            print('metadata keys:', list(meta.keys())[:30])
            for k in ('url','sourceURL','title','statusCode','ogTitle'):
                if k in meta:
                    print(' meta.' + k, '=', meta[k])
        for k in ('url','sourceURL','title'):
            if k in d:
                print(' top.' + k, '=', d[k])
    print()
