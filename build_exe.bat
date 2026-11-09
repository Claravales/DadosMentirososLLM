@echo off
rem Construye dist\Dados Mentirosos.exe  (NO abre la app: para abrirla usa "Abrir Dados Mentirosos.bat").
rem Solo hace falta Python para construirlo, no para usar el .exe.
cd /d "%~dp0"
set WORK=%TEMP%\dados-mentirosos-build
python -m pip install --quiet pyinstaller
if errorlevel 1 goto fallo
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name "Dados Mentirosos" ^
  --add-data "%~dp0liars_dice\web\index.html;liars_dice\web" ^
  --workpath "%WORK%" --specpath "%WORK%" --distpath "%~dp0dist" --log-level WARN web_app.py
if errorlevel 1 goto fallo
echo.
echo Listo: dist\Dados Mentirosos.exe
pause
exit /b 0
:fallo
echo.
echo ERROR: no se pudo construir. Cierra "Dados Mentirosos" si esta abierto y vuelve a intentarlo.
pause
exit /b 1
