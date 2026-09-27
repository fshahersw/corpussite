"""Keep the caller-provided judge key encrypted locally and out of command lines."""
from __future__ import annotations
import ctypes, getpass, json, os, pathlib, re, subprocess, sys
from run_collector import private_key

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')
CREDENTIAL = ROOT / '.auth' / 'firecrawl_judge.dpapi'
NODE = pathlib.Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
CLI = ROOT / '.tools/firecrawl-cli/node_modules/firecrawl-cli/dist/index.js'

def store():
    key = getpass.getpass('Private key: ').strip()
    if not re.fullmatch(r'fc-[0-9a-fA-F]{32}', key):
        raise ValueError('The supplied key has an unexpected format')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    data = key.encode('utf-16-le')
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, encrypted = Blob(len(data), buffer), Blob()
    crypt, kernel = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptProtectData(ctypes.byref(source), 'Judge collection credential', None, None, None, 1, ctypes.byref(encrypted)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        CREDENTIAL.parent.mkdir(parents=True, exist_ok=True)
        CREDENTIAL.write_text(ctypes.string_at(encrypted.data, encrypted.size).hex(), encoding='utf-8')
    finally:
        ctypes.memset(buffer, 0, len(data))
        kernel.LocalFree(encrypted.data)
    if private_key(CREDENTIAL) != key:
        raise RuntimeError('Encrypted credential verification failed')
    print(json.dumps({'configured': True, 'storage': 'Windows user-bound DPAPI', 'key_printed': False}))

def cli(args):
    if not CLI.is_file():
        raise FileNotFoundError('The workspace Firecrawl CLI is not installed')
    key = private_key(CREDENTIAL)
    env = {**os.environ, 'FIRECRAWL_API_KEY': key}
    env['NODE_OPTIONS'] = (env.get('NODE_OPTIONS', '') + ' --use-system-ca').strip()
    result = subprocess.run([str(NODE), str(CLI), *args], env=env, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    for stream, dest in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        print(re.sub(r'fc-[0-9a-fA-F]{20,}', '[REDACTED]', stream.replace(key, '[REDACTED]')), end='', file=dest)
    return result.returncode

def preflight():
    sys.path.insert(0, str(ROOT / 'pipeline'))
    from firecrawl_batch_worker import Client
    key = private_key(CREDENTIAL)
    status, body = Client(key).call('GET', 'team/credit-usage')
    data = body.get('data', {}) if isinstance(body, dict) else {}
    result = {'checked_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
              'http_status': status, 'remaining_credits': data.get('remainingCredits'),
              'total_credits': data.get('totalCredits'), 'credential_source': 'new_user_authorized_judge_key',
              'configured': status == 200, 'key_printed': False}
    output = ROOT / 'reports/judges/focus_20260913/credit_preflight.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if status == 200 else 1

if __name__ == '__main__':
    if sys.argv[1:] == ['store']:
        store()
    elif sys.argv[1:] == ['preflight']:
        raise SystemExit(preflight())
    else:
        raise SystemExit(cli(sys.argv[1:]))
