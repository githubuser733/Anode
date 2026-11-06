"""Check only cmdkey target metadata; never reads a credential password."""
import json
import subprocess
import sys


def main():
    result = subprocess.run(['cmdkey.exe', '/list'], capture_output=True, text=True,
                            errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'cmdkey failed: {result.returncode}')
    # Anchor the complete target instead of matching cmdkey's introductory heading.
    found = any(line.strip().lower().endswith('target=termsrv/localhost')
                for line in result.stdout.splitlines())
    print(json.dumps({'result': 'LOCALHOST_SAVED_TARGET_PRESENT' if found else
                      'LOCALHOST_SAVED_TARGET_MISSING', 'target': 'TERMSRV/localhost',
                      'passwordRead': False, 'scope': 'Target presence does not prove usable login.'}))
    return 0 if found else 1


if __name__ == '__main__':
    sys.exit(main())
