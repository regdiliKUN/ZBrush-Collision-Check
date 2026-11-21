@echo off
REM ============================================================
REM  ZBrush collision check - fallback launcher (pure ASCII).
REM  The macro prefers bin\ZBCollisionEngine.exe (no Python needed);
REM  this bat is used only when that exe is missing.
REM  "start" detaches pythonw so this console window closes at once.
REM ============================================================
set "SCRIPT_DIR=%~dp0"

where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw -3 "%SCRIPT_DIR%detect_and_show.pyw"
    goto :eof
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%SCRIPT_DIR%detect_and_show.pyw"
    goto :eof
)
where py >nul 2>&1
if %errorlevel%==0 (
    start "" /min py -3 "%SCRIPT_DIR%detect_and_show.pyw"
    goto :eof
)
where python >nul 2>&1
if %errorlevel%==0 (
    start "" /min python "%SCRIPT_DIR%detect_and_show.pyw"
    goto :eof
)

REM No Python and no engine exe: show the Chinese help page.
echo DETECT FAILED: engine exe and Python 3 not found. > "%SCRIPT_DIR%result.txt"
echo done > "%SCRIPT_DIR%done.flag"
if exist "%SCRIPT_DIR%help_no_python.html" start "" "%SCRIPT_DIR%help_no_python.html"
