"""Where saved document text lives in each layer (table, text column or text file path). Read-only survey used to plan the citation index."""
import glob
import os
import sqlite3

ROOT = 'C:/Users/firas/Downloads/SCRAPE/sources/'
for folder in ('court_document_library_20260919', 'agency_science_documents_20260919', 'saved_web_pages_20260919', 'uscourts_pages_20260919',
               'source_directory_documents_20260919', 'county_litigation_20260919', 'mdl_docket_documents_20260919', 'state_coordinated_proceedings_20260919'):
    for path in glob.glob(ROOT + folder + '/*.sqlite3'):
        path = path.replace(os.sep, '/')
        db = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
        tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like '%fts%' and name not like 'sqlite_%'")]
        print('==', path.replace(ROOT, ''), round(os.path.getsize(path) / 1e6), 'MB', tables)
        for table in tables[:5]:
            columns = [r[1] for r in db.execute('pragma table_info(%s)' % table)]
            count = db.execute('select count(*) from %s' % table).fetchone()[0]
            print('     ', table, count, columns[:24])
