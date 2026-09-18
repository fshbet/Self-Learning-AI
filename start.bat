@echo off
rem Start the Knowledge Platform: Docker (PostgreSQL), Ollama, then the API + embedded worker on port 8010.
setlocal
cd /d "%~dp0"

rem 1. Docker Desktop (the kp-postgres container restarts itself once the engine is up)
docker info >nul 2>&1
if errorlevel 1 (
    echo Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    :waitdocker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto waitdocker
)
docker compose up -d postgres

rem 2. Ollama (models qwen3:8b + nomic-embed-text)
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo Starting Ollama...
    start "" /min ollama serve
    timeout /t 5 /nobreak >nul
)

rem 3. Migrations, then the API (Ctrl+C stops it)
uv run kp db upgrade
start "" http://localhost:8010
uv run kp serve
