"""Run with native Windows Python: python test_console_runtime.py PATH_TO_WINDOWS_EXE.

The helper gets a new, hidden console; the runtime receives actual console
handles, not redirected pipes or the invoking user's console.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

RUNTIME = str(Path(sys.argv.pop(1)).resolve()) if len(sys.argv) > 1 else None

# A separate process owns the console so neither console attachment nor input
# injection can disturb the test runner or the user's active console.
HELPER = r'''
import ctypes
from ctypes import wintypes
import msvcrt
import os
import subprocess
import sys
import time

k = ctypes.WinDLL('kernel32', use_last_error=True)
HANDLE = wintypes.HANDLE
DWORD = wintypes.DWORD
WORD = wintypes.WORD
SHORT = ctypes.c_short

class COORD(ctypes.Structure):
    _fields_ = [('X', SHORT), ('Y', SHORT)]

class SMALL_RECT(ctypes.Structure):
    _fields_ = [('Left', SHORT), ('Top', SHORT), ('Right', SHORT), ('Bottom', SHORT)]

class SCREEN_INFO(ctypes.Structure):
    _fields_ = [('size', COORD), ('cursor', COORD), ('attributes', WORD),
                ('window', SMALL_RECT), ('maximum', COORD)]

class CHAR(ctypes.Union):
    _fields_ = [('UnicodeChar', wintypes.WCHAR), ('AsciiChar', ctypes.c_char)]

class KEY_EVENT(ctypes.Structure):
    _fields_ = [('down', wintypes.BOOL), ('repeat', WORD), ('vk', WORD),
                ('scan', WORD), ('char', CHAR), ('control', DWORD)]

# INPUT_RECORD's event union is 16 bytes (MOUSE_EVENT_RECORD is its largest
# member); specifying it matters when passing an array to Windows.
class EVENT(ctypes.Union):
    _fields_ = [('key', KEY_EVENT), ('padding', ctypes.c_byte * 16)]

class INPUT_RECORD(ctypes.Structure):
    _fields_ = [('type', WORD), ('event', EVENT)]

k.CreateFileW.argtypes = [wintypes.LPCWSTR, DWORD, DWORD, ctypes.c_void_p,
                          DWORD, DWORD, HANDLE]
k.CreateFileW.restype = HANDLE
k.GetConsoleMode.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
k.GetConsoleMode.restype = wintypes.BOOL
k.GetConsoleScreenBufferInfo.argtypes = [HANDLE, ctypes.POINTER(SCREEN_INFO)]
k.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
k.ReadConsoleOutputCharacterW.argtypes = [HANDLE, wintypes.LPWSTR, DWORD, COORD,
                                          ctypes.POINTER(DWORD)]
k.ReadConsoleOutputCharacterW.restype = wintypes.BOOL
k.WriteConsoleInputW.argtypes = [HANDLE, ctypes.POINTER(INPUT_RECORD), DWORD,
                                ctypes.POINTER(DWORD)]
k.WriteConsoleInputW.restype = wintypes.BOOL


def checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def console_file(name, flags):
    handle = k.CreateFileW(name, 0xC0000000, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, flags),
                     'rb' if flags == os.O_RDONLY else 'wb', buffering=0)


def screen(handle):
    info = SCREEN_INFO()
    checked(k.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)))
    count = info.size.X * (info.cursor.Y + 1)
    buf = ctypes.create_unicode_buffer(count + 1)
    read = DWORD()
    checked(k.ReadConsoleOutputCharacterW(handle, buf, count, COORD(0, 0),
                                           ctypes.byref(read)))
    return buf[:read.value]


def send(handle, text):
    records = (INPUT_RECORD * len(text))()
    for record, char in zip(records, text):
        record.type = 1  # KEY_EVENT
        record.event.key.down = True
        record.event.key.repeat = 1
        record.event.key.char.UnicodeChar = char
    written = DWORD()
    checked(k.WriteConsoleInputW(handle, records, len(records), ctypes.byref(written)))
    if written.value != len(records):
        raise RuntimeError('short console input write')


with console_file('CONIN$', os.O_RDONLY) as inp, console_file('CONOUT$', os.O_WRONLY) as out:
    ih = msvcrt.get_osfhandle(inp.fileno())
    oh = msvcrt.get_osfhandle(out.fileno())
    mode = DWORD()
    checked(k.GetConsoleMode(ih, ctypes.byref(mode)))
    checked(k.GetConsoleMode(oh, ctypes.byref(mode)))
    # Explicitly enter interactive mode; this runtime currently does not
    # select the REPL by default when launched without arguments.
    child = subprocess.Popen([sys.argv[1], '-i'], stdin=inp, stdout=out, stderr=out)
    try:
        def expect(text):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                contents = screen(oh)
                if text in contents:
                    return
                if child.poll() is not None:
                    raise AssertionError('runtime exited early: %r; screen=%r' %
                                         (child.returncode, contents))
                time.sleep(0.05)
            raise AssertionError('timed out waiting for %r; screen=%r' % (text, screen(oh)))

        expect('>>> ')
        send(ih, 'print(7381*9)\r')
        expect('66429')  # Not present in the typed command or initial screen.
        send(ih, '\x04')  # Ctrl-D at the next prompt exits the REPL.
        code = child.wait(timeout=8)
        if code != 0:
            raise AssertionError('runtime exit code %r; screen=%r' % (code, screen(oh)))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
'''


@unittest.skipUnless(os.name == 'nt', 'requires native Windows console APIs')
class ConsoleRuntime(unittest.TestCase):
    def test_console_repl_input_output_and_exit(self):
        if not RUNTIME:
            self.skipTest('pass the runtime executable as the first argument')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        helper = subprocess.Popen(
            [sys.executable, '-c', HELPER, RUNTIME],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        )
        try:
            stdout, stderr = helper.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # Terminate the whole isolated process tree, including a stuck runtime.
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(helper.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
            helper.kill()
            helper.communicate(timeout=5)
            self.fail('isolated console helper timed out')
        self.assertEqual(helper.returncode, 0, (stdout, stderr))


if __name__ == '__main__':
    unittest.main()
