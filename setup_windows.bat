@echo off
echo ==========================================
echo Configurando Ambiente Antigravity
echo ==========================================

echo 0. Procurando executavel Python...
set PYTHON_EXE=
python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_EXE=python
) else (
    py --version >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_EXE=py
    )
)

if "%PYTHON_EXE%"=="" (
    echo [ERRO] Python 3.10+ nao encontrado no PATH e o launcher 'py' falhou.
    echo Por favor, instale o Python marcando a caixa "Add Python to PATH" e reabra o terminal.
    pause
    exit /b 1
)

echo Usando executavel: %PYTHON_EXE%

echo 1. Criando ambiente virtual (.venv)...
%PYTHON_EXE% -m venv .venv

echo 2. Atualizando o pip...
.venv\Scripts\python -m pip install --upgrade pip

echo 3. Instalando dependencias (requirements.txt)...
.venv\Scripts\python -m pip install -r requirements.txt

echo 4. Instalando browsers do Playwright (Headless)...
.venv\Scripts\python -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] Falha ao instalar browsers do Playwright.
    pause
    exit /b %ERRORLEVEL%
)

echo 5. Validando o ambiente...
.venv\Scripts\python tools\check_env.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] Falha na validacao do ambiente.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ==========================================
echo Instalacao concluida com SUCESSO!
echo.
echo Exemplos de uso do motor (em run_windows.bat):
echo - Listar receitas:   run_windows.bat SP --listar-receitas
echo - Emitir com cache:  run_windows.bat SP --usar-cache --receita 046 --cnpj 12345678000199 --valor 100,00 --mes 03 --ano 2026
echo ==========================================
pause
