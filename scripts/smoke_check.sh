#!/usr/bin/env bash
set -e
python -m compileall .
python -c "from services.schema import init_db; init_db(); print('DB OK')"
echo OK
