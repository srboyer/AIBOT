' start_claude_watcher.vbs -- runs the watcher batch file with the console
' window hidden (the 0 in Run). Put a SHORTCUT to this file in the Startup
' folder (Win+R -> shell:startup) so the watcher launches on every boot.
' If you ever need to see the watcher's output for debugging, just run the
' .bat directly instead.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & "D:\AIBOT\Demo\start_claude_watcher.bat" & Chr(34), 0
Set WshShell = Nothing
