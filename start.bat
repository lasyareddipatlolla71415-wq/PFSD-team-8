@echo off
echo Starting Django backend...
start cmd /k "cd backend && python manage.py runserver"
timeout /t 3 /nobreak > nul
echo Starting Flask frontend...
start cmd /k "python flask_frontend.py"
echo.
echo Backend : http://localhost:8000
echo Frontend: http://localhost:3000
