@echo off
rem start_claude_watcher.bat -- launches the multi-app chat watcher.
rem The watcher's own outer loop handles everything being not-ready-yet at
rem boot: it retries the Bluetooth connection and quietly waits for each chat
rem app to open, so launch order after a restart doesn't matter.
rem Uses Classic Bluetooth SPP -- see claude_matrix_esp32_classicbt.ino.
rem --classic-bt auto-detects ClaudeStatusMatrix's COM port via
rem find_classic_bt_port() (claude_light_tracker.py) instead of hardcoding
rem one, since Windows assigns it per-machine and it can change on re-pair.
cd /d D:\AIBOT\Demo
py -3.11 chat_watcher.py --classic-bt
