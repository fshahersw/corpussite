"""Bounded offline Seeger derivatives. Originals and released imports are read-only."""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/seeger_text_recovery_20260918'
VERSION = '1.0.1'
STORIES = {1: 'MAIN BODY', 2: 'FOOTNOTES', 3: 'ENDNOTES', 4: 'COMMENTS',
           5: 'TEXT FRAME', 6: 'EVEN PAGE HEADER', 7: 'PRIMARY HEADER',
           8: 'EVEN PAGE FOOTER', 9: 'PRIMARY FOOTER', 10: 'FIRST PAGE HEADER',
           11: 'FIRST PAGE FOOTER'}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def lines(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def atomic(path, value, jsonl=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as f:
        if jsonl:
            for row in value:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(value, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def event(row):
    with (OUT / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'at': now(), **row}, ensure_ascii=False) + '\n')


def winword_pids():
    """Read process names without launching Word or querying other command lines."""
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        found = set()
        ok = api.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == 'winword.exe':
                found.add(int(entry.th32ProcessID))
            ok = api.Process32NextW(handle, ctypes.byref(entry))
        return found
    finally:
        api.CloseHandle(handle)


def terminate_owned_word(pid, preexisting):
    if not pid or pid in preexisting or pid not in winword_pids():
        return
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(1, False, pid)
    if handle:
        try:
            api.TerminateProcess(handle, 1)
            event({'event': 'terminated_own_unresponsive_word', 'pid': pid})
        finally:
            api.CloseHandle(handle)


def clean_word_text(text):
    # Keep meaningful field results and explicit boundaries; never update fields.
    rendered = text.replace('\r\x07', '\n[CELL END]\n').replace('\x07', '[CELL END]') \
        .replace('\r', '\n').replace('\x0b', '\n').replace('\x0c', '\n[PAGE BREAK]\n') \
        .replace('\x02', '[NOTE REFERENCE]').replace('\x0e', '\n[COLUMN BREAK]\n') \
        .replace('\x01', '[INLINE OBJECT]')
    return ''.join(c if ord(c) >= 32 or c in '\n\t' else f'[WORD CONTROL U+{ord(c):04X}]' for c in rendered).strip()


def story_text(story):
    original = str(story.Text or '')
    tables = []
    try:
        count = int(story.Tables.Count)
        for n in range(1, count + 1):
            table = story.Tables(n)
            tables.append({'table_number': n, 'start': int(table.Range.Start),
                           'end': int(table.Range.End), 'cells': int(table.Range.Cells.Count)})
    except Exception as exc:
        return clean_word_text(original), {'table_structure_error': str(exc), 'tables': tables}
    if not tables:
        return clean_word_text(original), {'tables': []}
    # Word Range text retains cell-end control codes, avoiding merged-cell guesses.
    result = []; position = int(story.Start)
    for table in tables:
        if table['start'] < position:  # nested table already represented by outer text
            continue
        segment = story.Duplicate
        segment.SetRange(position, table['start'])
        result.append(clean_word_text(str(segment.Text or '')))
        segment.SetRange(table['start'], table['end'])
        result.append(f"[TABLE {table['table_number']} START]\n" + clean_word_text(str(segment.Text or '')) + f"\n[TABLE {table['table_number']} END]")
        position = table['end']
    segment = story.Duplicate
    segment.SetRange(position, int(story.End))
    result.append(clean_word_text(str(segment.Text or '')))
    return '\n\n'.join(part for part in result if part), {'tables': tables}


def word_worker(connection, records, preexisting):
    import pythoncom
    import win32com.client
    import win32process
    pythoncom.CoInitialize()
    app = None; owned = False; probe = None
    try:
        app = win32com.client.DispatchEx('Word.Application')
        # Word exposes Hwnd on Window, rather than Application in this version.
        # A new unsaved blank document provides an authoritative instance window.
        app.AutomationSecurity = 3
        app.Visible = False
        app.DisplayAlerts = 0
        probe = app.Documents.Add(Visible=False)
        pid = int(win32process.GetWindowThreadProcessId(int(probe.Windows(1).Hwnd))[1])
        probe.Close(SaveChanges=0); probe = None
        if pid in preexisting:
            raise RuntimeError('DispatchEx returned a pre-existing Word process; refusing to operate it')
        owned = True
        connection.send({'event': 'word_started', 'pid': pid})
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        app.Options.UpdateLinksAtOpen = False
        app.Options.UpdateFieldsAtPrint = False
        app.Options.UpdateLinksAtPrint = False
        app.Options.SaveNormalPrompt = False
        app.Options.ConfirmConversions = False
        version = str(app.Version)
        security = {'dedicated_dispatch_ex': True, 'word_pid': pid, 'preexisting_word_pids': sorted(preexisting),
                    'visible': bool(app.Visible), 'automation_security': int(app.AutomationSecurity),
                    'update_links_at_open': bool(app.Options.UpdateLinksAtOpen),
                    'update_fields_at_print': bool(app.Options.UpdateFieldsAtPrint),
                    'update_links_at_print': bool(app.Options.UpdateLinksAtPrint),
                    'original_opened': False, 'input_is_verified_scratch_copy': True,
                    'read_only': True, 'add_to_recent_files': False, 'fields_explicitly_updated': False}
        if security['automation_security'] != 3 or security['visible'] or security['update_links_at_open']:
            raise RuntimeError('Required Word safety settings did not persist')
        connection.send({'event': 'word_ready', 'pid': pid, 'version': version, 'security': security})
        for row in records:
            connection.send({'event': 'document_started', 'id': row['id']})
            doc = None
            started = time.monotonic()
            source = ROOT / row['raw_path']
            try:
                before = sha(source)
                if before != row['sha256']:
                    raise ValueError('Original hash differs from released manifest')
                scratch = OUT / 'scratch' / (row['id'] + source.suffix)
                scratch.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, scratch)
                if sha(scratch) != before:
                    raise ValueError('Scratch copy hash mismatch')
                doc = app.Documents.OpenNoRepairDialog(FileName=str(scratch), ConfirmConversions=False,
                    ReadOnly=True, AddToRecentFiles=False, PasswordDocument='__no_password_supplied__',
                    PasswordTemplate='__no_password_supplied__', WritePasswordDocument='__no_password_supplied__',
                    WritePasswordTemplate='__no_password_supplied__', Revert=False, Visible=False,
                    OpenAndRepair=False, NoEncodingDialog=True)
                if not bool(doc.ReadOnly):
                    raise ValueError('Word did not open scratch document read-only')
                chunks = []; stories = []; warnings = []
                for story_type, label in STORIES.items():
                    try:
                        story = doc.StoryRanges(story_type)
                    except pythoncom.com_error:
                        continue  # this story type is absent
                    number = 0
                    while story is not None:
                        number += 1
                        if number > 500:
                            raise ValueError('Story traversal exceeded bounded part limit')
                        text, structure = story_text(story)
                        stories.append({'story_type': story_type, 'label': label, 'part': number,
                                        'characters': len(text), **structure})
                        if structure.get('table_structure_error'):
                            warnings.append('A table boundary query failed; original cell-end markers retained')
                        if text:
                            chunks.append(f'===== {label} / PART {number} =====\n{text}')
                        story = story.NextStoryRange
                text = '\n\n'.join(chunks).strip() + '\n'
                if not any(s['story_type'] == 1 and s['characters'] > 0 for s in stories):
                    raise ValueError('Main body has no recovered text')
                doc.Close(SaveChanges=0); doc = None
                after = sha(source)
                if after != before:
                    raise ValueError('Original changed during extraction')
                text_path = OUT / 'text' / (row['id'] + '.txt')
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(text, encoding='utf-8', newline='\n')
                metadata_path = OUT / 'metadata' / (row['id'] + '.json')
                metadata = {'id': row['id'], 'source_url': row['source_url'], 'title': row['title'],
                    'raw_path': row['raw_path'], 'raw_sha256_before': before, 'raw_sha256_after': after,
                    'scratch_path': relative(scratch), 'parser': 'Microsoft Word COM StoryRanges/Range.Text',
                    'parser_version': version, 'recovery_script_version': VERSION, 'method': 'word_com_native',
                    'captured_at': now(), 'elapsed_seconds': round(time.monotonic() - started, 3),
                    'security': security, 'stories': stories, 'quality_notes': warnings + [
                        'Existing field results read without updating fields or links.',
                        'Table and cell boundaries labeled; merged-cell layout is not reconstructed.',
                        'Headers, footers, notes and comments appear in separately labeled story order.',
                        'OCR not performed on embedded images; legal currency not verified.'],
                    'text_path': relative(text_path), 'text_sha256': sha(text_path), 'characters': len(text)}
                atomic(metadata_path, metadata)
                connection.send({'event': 'recovered', 'record': {'id': row['id'], 'raw_sha256': before,
                    'text_path': relative(text_path), 'text_sha256': metadata['text_sha256'], 'characters': len(text),
                    'method': 'word_com_native', 'metadata_path': relative(metadata_path),
                    'metadata_sha256': sha(metadata_path), 'status': 'recovered', 'quality_notes': metadata['quality_notes']}})
            except Exception as exc:
                connection.send({'event': 'failed', 'id': row['id'], 'error': type(exc).__name__ + ': ' + str(exc),
                                 'raw_sha256_after': sha(source) if source.exists() else None})
            finally:
                if doc is not None:
                    try: doc.Close(SaveChanges=0)
                    except Exception: pass
        connection.send({'event': 'worker_finished'})
    except Exception as exc:
        connection.send({'event': 'worker_error', 'error': type(exc).__name__ + ': ' + str(exc)})
    finally:
        if probe is not None:
            try: probe.Close(SaveChanges=0)
            except Exception: pass
        if owned and app is not None:
            try: app.Quit(SaveChanges=0)
            except Exception: pass
        pythoncom.CoUninitialize()
        connection.close()


def load_input():
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = OUT / 'input_originals.jsonl'
    if frozen.exists():
        return lines(frozen)
    rows = [r for r in lines(ROOT / 'sources/seeger_import_20260918/resources.jsonl')
            if r['id'].startswith('seeger_original_') and not r.get('text_path')]
    if len(rows) != 385:
        raise ValueError(f'Expected frozen 385 empty-native originals, found {len(rows)}')
    atomic(frozen, rows, True)
    atomic(OUT / 'input_receipt.json', {'created_at': now(), 'rows': len(rows),
        'input_manifest_sha256': sha(frozen), 'released_manifest_sha256': sha(ROOT / 'sources/seeger_import_20260918/resources.jsonl'),
        'scope': 'Only 385 original records without text; partial-native PDF gaps remain outside this derivative pass'})
    return rows


def refresh(rows, *, final=False):
    events = lines(OUT / 'events.jsonl')
    recovered = {e['record']['id']: e['record'] for e in events if e['event'] == 'recovered'}
    failures = {e['id']: e for e in events if e['event'] == 'failed' and e.get('id')}
    atomic(OUT / 'resources.jsonl', list(recovered.values()), True)
    atomic(OUT / 'failures.jsonl', [v for k, v in failures.items() if k not in recovered], True)
    remaining = [r for r in rows if r['id'] not in recovered]
    atomic(OUT / 'unresolved.jsonl', [{'id': r['id'], 'raw_path': r['raw_path'], 'raw_sha256': r['sha256'],
        'title': r['title'], 'status': 'failed' if r['id'] in failures else 'not_yet_recovered',
        'error': failures.get(r['id'], {}).get('error')} for r in remaining], True)
    summary = {'generated_at': now(), 'input_originals': len(rows), 'recovered': len(recovered),
        'by_method': dict(Counter(r['method'] for r in recovered.values())), 'gaps_remaining': len(remaining),
        'remaining_extensions': dict(Counter(Path(r['raw_path']).suffix for r in remaining)),
        'status': 'finished' if final else 'in_progress', 'new_corpus_records': 0,
        'original_records_should_receive_derivative_overlay': True,
        'partial_native_pdf_and_truncated_xml_gaps_unchanged': True}
    atomic(OUT / 'summary.json', summary)
    return recovered


def recover_word(rows, pilot=False):
    done = refresh(rows)
    done = {key: record for key, record in done.items()
            if json.loads((ROOT / record['metadata_path']).read_text(encoding='utf-8')).get('recovery_script_version') == VERSION}
    eligible = [r for r in rows if Path(r['raw_path']).suffix.lower() in {'.doc', '.rtf'} and r['id'] not in done]
    if pilot:
        eligible = [r for r in eligible if Path(r['raw_path']).suffix == '.doc'][:4] + [r for r in eligible if Path(r['raw_path']).suffix == '.rtf'][:2]
    ctx = mp.get_context('spawn')
    for start in range(0, len(eligible), 30):
        batch = eligible[start:start + 30]
        pending = list(batch)
        while pending:
            pending_at_start = {r['id'] for r in pending}
            preexisting = winword_pids()
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(target=word_worker, args=(child, pending, preexisting))
            process.start(); child.close()
            own_pid = None; current = None; deadline = time.monotonic() + 60; finished = False
            while process.is_alive() or parent.poll():
                if parent.poll(0.2):
                    try: message = parent.recv()
                    except EOFError: break
                    event(message)
                    typ = message['event']
                    if typ == 'word_started': own_pid = message['pid']
                    if typ == 'document_started':
                        current = message['id']; deadline = time.monotonic() + 45
                    if typ in {'recovered', 'failed'}:
                        key = message['record']['id'] if typ == 'recovered' else message['id']
                        pending = [r for r in pending if r['id'] != key]
                        current = None; deadline = time.monotonic() + 45
                        refresh(rows)
                        print(json.dumps({'event': typ, 'id': key, 'remaining_this_chunk': len(pending),
                                          'characters': message.get('record', {}).get('characters'), 'error': message.get('error')}), flush=True)
                    if typ == 'worker_finished': finished = True; deadline = time.monotonic() + 15
                    if typ == 'worker_error':
                        print(json.dumps(message), flush=True); break
                if time.monotonic() > deadline:
                    if current:
                        event({'event': 'failed', 'id': current, 'error': 'Per-document 45-second timeout'})
                        pending = [r for r in pending if r['id'] != current]
                    break
            process.join(3)
            if process.is_alive(): process.terminate(); process.join(3)
            terminate_owned_word(own_pid, preexisting)
            parent.close()
            if not finished and current is None and {r['id'] for r in pending} == pending_at_start:
                raise RuntimeError('Word startup failed; refusing an unbounded retry')
            refresh(rows)
    if pilot:
        atomic(OUT / 'pilot.json', {'at': now(), 'selected_ids': [r['id'] for r in eligible],
            'recovered': sum(r['id'] in refresh(rows) for r in eligible)})


def recover_ocr(rows):
    done = refresh(rows)
    pdfs = [r for r in rows if Path(r['raw_path']).suffix == '.pdf' and r['id'] not in done]
    if not pdfs: return
    spec = importlib.util.spec_from_file_location('court_ocr_prepare', ROOT / 'pipeline/prepare_court_ocr.py')
    prepare = importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare)
    ocr_root = OUT / 'ocr'
    snapshot = [{'id': r['id'], 'url': r['source_url'], 'raw_path': r['raw_path'],
                 'sha256': r['sha256'], 'text_path': None} for r in pdfs]
    prepare.prepare(ROOT, ocr_root, ROOT / 'sources/official_courts/ocr/models',
                    resource_snapshot=snapshot, snapshot_kind='frozen_385_empty_seeger_originals_two_scanned_pdfs')
    env = {**os.environ, 'OFFICIAL_OCR_ROOT': str(ocr_root), 'OFFICIAL_OCR_RUN_SECONDS': '240',
           'OFFICIAL_OCR_WORKERS': '1', 'OFFICIAL_OCR_PAGE_TIMEOUT_MS': '90000'}
    subprocess.run(['C:/Program Files/nodejs/node.exe', str(ROOT / 'sources/official_courts/ocr_worker.cjs')],
                   env=env, check=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    manifests = {r['source_pdf_sha256']: r for r in lines(ocr_root / 'pdf_ocr_manifest.jsonl')}
    for row in pdfs:
        derivative = manifests.get(row['sha256'])
        if not derivative or derivative.get('ocr_status') != 'complete':
            event({'event': 'failed', 'id': row['id'], 'error': 'OCR incomplete'})
            continue
        if sha(ROOT / row['raw_path']) != row['sha256']:
            raise ValueError('Original PDF changed during OCR')
        path = Path(derivative['ocr_text_path']); text = path.read_text(encoding='utf-8')
        if sha(path) != derivative['ocr_text_sha256'] or not text.strip():
            raise ValueError('OCR text verification failed')
        notes = ['OCR is machine transcription; review against original images.',
                 'Confidence is an engine estimate, not a correctness guarantee; legal currency not verified.']
        metadata_path = OUT / 'metadata' / (row['id'] + '.json')
        atomic(metadata_path, {'id': row['id'], 'raw_sha256_before': row['sha256'],
            'raw_sha256_after': sha(ROOT / row['raw_path']), 'method': 'tesseract_js_ocr',
            'captured_at': now(), 'quality_notes': notes, 'derivative': derivative})
        event({'event': 'recovered', 'record': {'id': row['id'], 'raw_sha256': row['sha256'],
            'text_path': relative(path), 'text_sha256': sha(path), 'characters': len(text),
            'method': 'tesseract_js_ocr', 'metadata_path': relative(metadata_path),
            'metadata_sha256': sha(metadata_path), 'status': 'recovered', 'quality_notes': notes}})
    refresh(rows)


def validate(rows, final=False):
    recovered = refresh(rows, final=final); errors = []
    source_rows = {r['id']: r for r in rows}
    for key, record in recovered.items():
        original = source_rows[key]
        try:
            if record['raw_sha256'] != original['sha256'] or sha(ROOT / original['raw_path']) != original['sha256']:
                raise ValueError('Original SHA mismatch')
            for name in ['text', 'metadata']:
                path = (ROOT / record[name + '_path']).resolve()
                if not path.is_relative_to(OUT) or sha(path) != record[name + '_sha256']:
                    raise ValueError(name + ' derivative scope/hash mismatch')
            text = (ROOT / record['text_path']).read_text(encoding='utf-8', errors='strict')
            if len(text) != record['characters'] or not text.strip(): raise ValueError('Invalid body text')
            metadata = json.loads((ROOT / record['metadata_path']).read_text(encoding='utf-8'))
            if metadata['id'] != key or metadata['raw_sha256_before'] != original['sha256'] or metadata['raw_sha256_after'] != original['sha256']:
                raise ValueError('Metadata original binding mismatch')
        except Exception as exc:
            errors.append({'id': key, 'error': str(exc)})
    for row in rows:
        if sha(ROOT / row['raw_path']) != row['sha256']:
            errors.append({'id': row['id'], 'error': 'Source original changed'})
    report = {'generated_at': now(), 'status': 'passed' if not errors and final else 'failed' if errors else 'pilot_passed',
        'verified_recovered_records': len(recovered), 'verified_unchanged_originals': len(rows), 'errors': errors,
        'resources_sha256': sha(OUT / 'resources.jsonl'), 'input_manifest_sha256': sha(OUT / 'input_originals.jsonl'),
        'overlay_ready': not errors and final, 'source_records_duplicated': False}
    atomic(OUT / 'validation.json', report)
    print(json.dumps(report), flush=True)
    if errors: raise RuntimeError('Validation failed')


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--pilot', action='store_true')
    args.add_argument('--all', action='store_true')
    args.add_argument('--validate', action='store_true')
    options = args.parse_args()
    originals = load_input()
    if options.pilot: recover_word(originals, pilot=True)
    if options.all:
        recover_word(originals)
        recover_ocr(originals)
    validate(originals, final=options.all or options.validate)
