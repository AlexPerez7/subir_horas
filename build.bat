@echo off
REM Recompila el .exe y deja dist/ listo para usar de una: copia
REM registro_horas.html y .env automaticamente despues de compilar,
REM para no tener que hacerlo a mano cada vez.

echo Compilando...
pyinstaller --onefile --windowed --name "RegistroHoras" app_escritorio.py

echo Copiando archivos necesarios a dist/...
copy /Y registro_horas.html dist\registro_horas.html
copy /Y .env dist\.env

echo.
echo Listo. dist\RegistroHoras.exe ya esta actualizado.
pause
