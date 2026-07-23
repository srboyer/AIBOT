@echo off
rem start_claude_watcher.bat -- launches the multi-app chat watcher.
rem The watcher's own outer loop handles everything being not-ready-yet at
rem boot: it retries the BLE connection and quietly waits for each chat app
rem to open, so launch order after a restart doesn't matter.
cd /d D:\AIBOT\Demo
py -3.11 chat_watcher.py --ble
