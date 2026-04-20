@echo off
python -m compileall .
python -c "from services.schema import init_db; init_db(); print('DB OK')"
echo OK
pause
