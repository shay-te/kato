@echo off
rem ============================================================
rem Windows entry point that mirrors the POSIX Makefile.
rem
rem Calls make.ps1 with -ExecutionPolicy Bypass so it runs without
rem the operator having to relax their PowerShell policy. Every
rem target lives in make.ps1 — this file is just a 5-line
rem dispatcher so cmd.exe users can type the same ``make X``
rem command as macOS/Linux users.
rem ============================================================

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0make.ps1" %*
exit /b %ERRORLEVEL%
