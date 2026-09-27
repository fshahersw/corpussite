"""Local ingestion credential helper. Never prints or serializes a plaintext key."""
from pathlib import Path
import getpass
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL = ROOT / '.auth/supabase_corpussite.dpapi'


def secret():
    value = os.environ.get('CORPUS_SUPABASE_SECRET_KEY')
    if value:
        return value
    import ctypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    encrypted = bytes.fromhex(CREDENTIAL.read_text(encoding='utf-8-sig').strip())
    buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
    source, decoded = Blob(len(encrypted), buffer), Blob()
    crypt, kernel = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(decoded)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value = ctypes.string_at(decoded.data, decoded.size).decode('utf-16-le')
    finally:
        ctypes.memset(decoded.data, 0, decoded.size)
        kernel.LocalFree(decoded.data)
    if not value.startswith('sb_secret_'):
        raise ValueError('Invalid stored Supabase credential')
    return value


def store():
    import ctypes
    key = getpass.getpass('Supabase server key: ').strip()
    if not key.startswith('sb_secret_') or len(key) < 30:
        raise ValueError('Expected a Supabase secret key')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    data = key.encode('utf-16-le')
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, encrypted = Blob(len(data), buffer), Blob()
    crypt, kernel = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not crypt.CryptProtectData(ctypes.byref(source), 'Corpus Supabase migration', None, None, None, 1, ctypes.byref(encrypted)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        CREDENTIAL.parent.mkdir(exist_ok=True)
        CREDENTIAL.write_text(ctypes.string_at(encrypted.data, encrypted.size).hex(), encoding='utf-8')
    finally:
        ctypes.memset(buffer, 0, len(data))
        kernel.LocalFree(encrypted.data)
    if secret() != key:
        raise RuntimeError('Credential verification failed')
    print('Stored user-bound encrypted credential. Plaintext was not written.')


if __name__ == '__main__':
    store()
