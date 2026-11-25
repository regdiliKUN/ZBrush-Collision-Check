@echo off
REM Rebuild macros + frozen exes after editing engine/ or installer/ (needs Python 3 + numpy + pyinstaller).
cd /d "%~dp0.."
py -3 zscript\generate_macro.py || goto :err
py -3 zscript\generate_macro.py --lang en || goto :err
copy /y engine\detect_and_show.pyw "%TEMP%\ZBCollisionEngine.py" >nul
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name ZBCollisionEngine --paths engine --hidden-import i18n --hidden-import collision_engine --hidden-import result_window --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module PIL --distpath dist --workpath "%TEMP%\zbc_build" --specpath "%TEMP%\zbc_build" "%TEMP%\ZBCollisionEngine.py" || goto :err
py -3 -m PyInstaller --noconfirm --clean --onefile --console --name ZBCollisionInstaller --exclude-module numpy --exclude-module tkinter --distpath . --workpath "%TEMP%\zbc_build2" --specpath "%TEMP%\zbc_build2" installer\install.py || goto :err
echo BUILD OK
pause
goto :eof
:err
echo BUILD FAILED
pause
