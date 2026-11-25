@echo off
REM English UI installer (pure ASCII). Same as the Chinese installer with --lang en.
cd /d "%~dp0"
"%~dp0ZBCollisionInstaller.exe" --lang en %*
