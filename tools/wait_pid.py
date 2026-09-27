"""Blocks until the given Windows process ends (or returns at once if it is already gone):  python tools/wait_pid.py <pid>"""
import ctypes
import sys

SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(sys.argv[1]))
if handle:
    ctypes.windll.kernel32.WaitForSingleObject(handle, INFINITE)
    ctypes.windll.kernel32.CloseHandle(handle)
print('process', sys.argv[1], 'has ended')
