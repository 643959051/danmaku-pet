from __future__ import annotations

import subprocess
import sys

url = "http://127.0.0.1:8787/"
if len(sys.argv) > 1 and sys.argv[1] == "cmd":
    command = ["cmd.exe", "/c", "start", "", url]
else:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f"Start-Process -FilePath '{url}'",
    ]
print("launch", command)
subprocess.Popen(command)
print("requested")
