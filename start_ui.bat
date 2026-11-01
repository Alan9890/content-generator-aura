@echo off
cd /d "%~dp0"
if exist "target\release\video_recreator_agent.exe" (
    echo Iniciando motor nativo en Rust...
    start "" "target\release\video_recreator_agent.exe"
) else (
    echo Iniciando motor ligero zero-dependencies...
    node server_light.js
)
