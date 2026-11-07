"""Single, console-free human entry: open the current Anode or start its verified version."""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = [str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
               '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(root / 'scripts/Start-AnodeIntegrated.ps1')]
    if args.check:
        command.append('-CheckOnly')
    # A PowerShell 7 parent can inject an incompatible PSModulePath into 5.1.
    # Let the Windows PowerShell child populate its own standard module paths.
    environment = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
    process = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip() or 'ANODE_ENTRY_FAILED'
        if args.check:
            print(detail, file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(None, detail, 'Anode 启动未完成', 16)
    elif args.check:
        print(process.stdout.strip())
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
