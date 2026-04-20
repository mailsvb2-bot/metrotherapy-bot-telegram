from __future__ import annotations

import json
import os
import urllib.request


HOST = (os.getenv('HEALTHCHECK_HOST', '127.0.0.1') or '127.0.0.1').strip()
PORT = int(os.getenv('HEALTHCHECK_PORT', '8082') or 8082)
URL = f'http://{HOST}:{PORT}/health'


def main() -> int:
    with urllib.request.urlopen(URL, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f'Healthcheck failed: HTTP {response.status}')
        payload = json.loads(response.read().decode('utf-8'))
    if not payload.get('ok'):
        raise SystemExit(f'Healthcheck failed: {payload}')
    if not payload.get('db_ready'):
        raise SystemExit(f'Database not ready: {payload}')
    if not payload.get('schema_ready'):
        raise SystemExit(f'Schema not ready: {payload}')
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
