@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title PUBG วัดระยะ - ตรวจความพร้อม
set "APP=%~dp0PUBG_Measure"
if not exist "%APP%\66031036_pubg_measure.py" if exist "%~dp066031036_pubg_measure.py" set "APP=%~dp0."
set "PKGS=opencv-python numpy mss keyboard sounddevice"

echo ==============================================
echo   PUBG วัดระยะ : ตรวจความพร้อมก่อนเปิด
echo ==============================================

rem ---------- 1) ไฟล์โปรแกรม
if not exist "%APP%\66031036_pubg_measure.py" (
  echo [X] ไม่พบไฟล์โปรแกรมใน "%APP%"
  echo     ต้องมีโฟลเดอร์ PUBG_Measure อยู่ข้างไฟล์นี้
  pause & exit /b 1
)
echo [OK] ไฟล์โปรแกรม

rem ---------- 2) Python
call :find_python
if not defined PY (
  echo [..] ไม่พบ Python กำลังติดตั้ง Python 3.12 ผ่าน winget...
  where winget >nul 2>nul || (
    echo [X] เครื่องนี้ไม่มี winget ติดตั้ง Python เองได้ที่ https://www.python.org/downloads/
    echo     ตอนติดตั้งให้ติ๊ก "Add python.exe to PATH" แล้วดับเบิลคลิกไฟล์นี้อีกครั้ง
    pause & exit /b 1
  )
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  call :find_python
  if not defined PY (
    echo [X] ติดตั้ง Python ไม่สำเร็จ ลองติดตั้งเองจาก https://www.python.org/downloads/
    pause & exit /b 1
  )
)
for /f "delims=" %%v in ('"!PY!" -c "import sys;print('%%d.%%d'%%sys.version_info[:2])"') do set "PYVER=%%v"
echo [OK] Python !PYVER!
"!PY!" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" || (
  echo [X] Python เก่าเกินไป ต้องเป็น 3.9 ขึ้นไป
  pause & exit /b 1
)
"!PY!" -c "import tkinter" >nul 2>nul || (
  echo [X] Python ตัวนี้ไม่มี tkinter ติดตั้ง Python จาก python.org ใหม่ แล้วลองอีกครั้ง
  pause & exit /b 1
)

rem ---------- 3) ไลบรารี
"!PY!" -c "import cv2, numpy, mss, keyboard, sounddevice" >nul 2>nul
if errorlevel 1 (
  echo [..] ไลบรารียังไม่ครบ กำลังติดตั้ง ^(ครั้งแรกใช้เวลา 1-2 นาที^)...
  "!PY!" -m pip install --user --disable-pip-version-check %PKGS%
  "!PY!" -c "import cv2, numpy, mss, keyboard" >nul 2>nul || (
    echo [X] ติดตั้งไลบรารีไม่สำเร็จ เช็กอินเทอร์เน็ตแล้วลองใหม่
    pause & exit /b 1
  )
  "!PY!" -c "import sounddevice" >nul 2>nul || echo [*] ติดตั้ง sounddevice ไม่ได้ คำสั่งเสียงจะใช้ไม่ได้ แต่ F8 ใช้ได้ปกติ
)
echo [OK] ไลบรารีครบ

rem ---------- 4) เปิดโปรแกรม (ไม่มีหน้าต่างดำค้าง)
set "PYW="
for %%d in ("!PY!") do if exist "%%~dpdpythonw.exe" set "PYW=%%~dpdpythonw.exe"
if not defined PYW set "PYW=!PY!"
echo [OK] กำลังเปิดโปรแกรม...
cd /d "%APP%"
start "" "!PYW!" "66031036_pubg_measure.py"
timeout /t 2 >nul
exit /b 0

:find_python
set "PY="
for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
if defined PY exit /b 0
for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
if defined PY exit /b 0
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if defined PY exit /b 0
for /d %%d in ("%ProgramFiles%\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
exit /b 0
