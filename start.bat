@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo Setting up for the first time...
  python -m venv venv
  call venv\Scripts\activate.bat
  pip install -r requirements.txt
)
start "" http://localhost:5000
venv\Scripts\python app.py