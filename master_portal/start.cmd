@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto error
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
.venv\Scripts\python.exe -X utf8 manage.py migrate
if errorlevel 1 goto error
.venv\Scripts\python.exe -X utf8 manage.py demo_data
if errorlevel 1 goto error
.venv\Scripts\python.exe -X utf8 manage.py family_demo
if errorlevel 1 goto error
.venv\Scripts\python.exe -X utf8 manage.py family_reminders
if errorlevel 1 goto error
.venv\Scripts\python.exe -X utf8 manage.py setup_master
if errorlevel 1 goto error
echo.
echo http://127.0.0.1:8000
.venv\Scripts\python.exe -X utf8 manage.py runserver 127.0.0.1:8000
exit /b
:error
echo Setup failed. Review the error above.
pause
exit /b 1
