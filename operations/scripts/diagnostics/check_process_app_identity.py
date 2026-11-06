"""Read Windows package/application identity; never changes a target process."""
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sys


def inspect(process_id):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x1000, False, process_id)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = {'pid': process_id}
        for function_name, key, absent in [('GetPackageFullName', 'package', 15700),
                                           ('GetApplicationUserModelId', 'applicationId', 15703)]:
            query = getattr(kernel, function_name)
            query.argtypes = [w.HANDLE, ctypes.POINTER(w.UINT), w.LPWSTR]
            query.restype = w.LONG
            length = w.UINT(0)
            code = query(handle, ctypes.byref(length), None)
            if code == absent:
                result[key] = None
                continue
            if code != 122:
                raise OSError(code, function_name)
            buffer = ctypes.create_unicode_buffer(length.value)
            code = query(handle, ctypes.byref(length), buffer)
            if code:
                raise OSError(code, function_name)
            result[key] = buffer.value
        return result
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pids', nargs='*', type=int)
    parser.add_argument('--self', action='store_true', dest='inspect_self')
    parser.add_argument('--assert-not-codex', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    pids = args.pids + ([os.getpid()] if args.inspect_self else [])
    if not pids:
        parser.error('Provide a pid or --self')
    rows = [inspect(pid) for pid in pids]
    contaminated = [row['pid'] for row in rows if any('openai.codex' in (row[key] or '').casefold()
                    for key in ('package', 'applicationId'))]
    result = {'processes': rows, 'codexIdentityPids': contaminated,
              'readOnly': True, 'windowGroupingNotInspected': True}
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
    return 1 if args.assert_not_codex and contaminated else 0


if __name__ == '__main__':
    sys.exit(main())
