@echo off
rem 默认使用 faster-whisper 高精度识别（模型首次运行自动下载）
python -c "import vosk, pyperclip, pyautogui, faster_whisper" 2>nul || pip install vosk pyperclip pyautogui faster-whisper sounddevice numpy
python "%~dp0listener.py" %*
pause
