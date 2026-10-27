"""Sends real X11 input to the virtual display (XTest), for scripts/orb-check.mjs. Linux with Xvfb only.

  python3 xtest.py key ctrl+alt+j        press and release a key chord
  python3 xtest.py move X Y              move the pointer
  python3 xtest.py click [left|right]    press and release a button
  python3 xtest.py down | up             hold or release the left button
"""
import ctypes
import ctypes.util
import sys
import time

x11 = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
xtst = ctypes.CDLL(ctypes.util.find_library('Xtst') or 'libXtst.so.6')
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XStringToKeysym.restype = ctypes.c_ulong
x11.XKeysymToKeycode.restype = ctypes.c_ubyte
x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
display = x11.XOpenDisplay(None)
if not display:
    sys.exit('no X display')

MODIFIERS = {'ctrl': 'Control_L', 'alt': 'Alt_L', 'shift': 'Shift_L', 'super': 'Super_L'}


def keycode(name):
    return x11.XKeysymToKeycode(display, x11.XStringToKeysym(name.encode()))


def key(chord):
    names = [MODIFIERS.get(part, part) for part in chord.lower().split('+')]
    codes = [keycode(name) for name in names]
    for code in codes:
        xtst.XTestFakeKeyEvent(ctypes.c_void_p(display), code, 1, 0)
        x11.XFlush(ctypes.c_void_p(display))
        time.sleep(0.03)
    for code in reversed(codes):
        xtst.XTestFakeKeyEvent(ctypes.c_void_p(display), code, 0, 0)
        x11.XFlush(ctypes.c_void_p(display))
        time.sleep(0.03)


def move(x, y):
    xtst.XTestFakeMotionEvent(ctypes.c_void_p(display), -1, int(x), int(y), 0)
    x11.XFlush(ctypes.c_void_p(display))


def button(number, down):
    xtst.XTestFakeButtonEvent(ctypes.c_void_p(display), number, 1 if down else 0, 0)
    x11.XFlush(ctypes.c_void_p(display))


command = sys.argv[1]
if command == 'key':
    key(sys.argv[2])
elif command == 'move':
    move(sys.argv[2], sys.argv[3])
elif command == 'click':
    which = 3 if len(sys.argv) > 2 and sys.argv[2] == 'right' else 1
    button(which, True)
    time.sleep(0.05)
    button(which, False)
elif command == 'down':
    button(1, True)
elif command == 'up':
    button(1, False)
else:
    sys.exit('unknown command')
