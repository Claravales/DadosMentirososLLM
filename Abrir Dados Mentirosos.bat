@echo off
rem Abre Dados Mentirosos.
rem Usa Python (esta firmado, asi que Windows no lo bloquea). El .exe, que NO esta firmado, solo se usa si no hay Python.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "Dados Mentirosos.pyw"
  exit /b 0
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "Dados Mentirosos.pyw"
  exit /b 0
)
if exist "dist\Dados Mentirosos.exe" (
  start "" "dist\Dados Mentirosos.exe"
  exit /b 0
)
echo No se encontro Python ni dist\Dados Mentirosos.exe.
echo Instala Python desde https://www.python.org/downloads/ y vuelve a abrir este archivo.
pause
