"""Explicit host/auth policy for the optional persistent archive backend."""
from __future__ import annotations

import hmac
import os


def configuration(bind: str, environ=None):
    env = os.environ if environ is None else environ
    token = env.get('CORPUS_BACKEND_TOKEN', '')
    hosts = frozenset(x.strip().lower() for x in env.get('CORPUS_ALLOWED_HOSTS', '').split(',') if x.strip())
    remote = bind not in {'127.0.0.1', 'localhost', '::1'}
    if remote and (len(token) < 32 or not hosts):
        raise ValueError('Remote binding requires CORPUS_BACKEND_TOKEN (32+ characters) and CORPUS_ALLOWED_HOSTS')
    if token and len(token) < 32:
        raise ValueError('CORPUS_BACKEND_TOKEN must contain at least 32 characters')
    if any('/' in host or '@' in host or '*' in host for host in hosts):
        raise ValueError('CORPUS_ALLOWED_HOSTS must contain exact host[:port] values, without wildcards')
    return {'token': token, 'hosts': hosts, 'remote': remote}


def authorize(headers, port: int, config):
    host = headers.get('Host', '').lower()
    allowed = set(config['hosts']) | {f'127.0.0.1:{port}', f'localhost:{port}'}
    if host not in allowed:
        return 403, 'Host is not allowed'
    token = config['token']
    if token and not hmac.compare_digest(headers.get('X-Corpus-Token', '').encode(), token.encode()):
        return 401, 'Backend authentication required'
    return None
