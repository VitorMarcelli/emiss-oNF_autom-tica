@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] O ambiente virtual nao foi encontrado. Por favor, execute setup_windows.bat primeiro!
    pause
    exit /b 1
)

.venv\Scripts\python tools\testar_uf.py %*
endlocal
