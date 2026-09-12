@echo off
rem 使用 Vosk 大模型（1.3GB，首次运行自动下载）
python -c "import vosk, pyperclip, pyautogui" 2>nul || pip install vosk pyperclip pyautogui sounddevice numpy
python "%~dp0listener.py" --model large %*
pause
