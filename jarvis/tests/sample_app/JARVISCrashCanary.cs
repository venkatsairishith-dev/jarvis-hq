// A harmless crash canary for JARVIS's proactive monitor (2026-10-16). It turns off the Windows error popup for its own
// process (SetErrorMode), then throws an unhandled exception, so Windows writes real Application-log crash events
// (.NET Runtime 1026 / Application Error 1000) without anything appearing on screen. It touches no files.
// Build: build.ps1 -Canary (the .NET Framework compiler that ships with Windows).
using System;
using System.Runtime.InteropServices;

static class JARVISCrashCanary {
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);

    static int Main() {
        SetErrorMode(0x0001 | 0x0002 | 0x8000);   // SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        throw new InvalidOperationException("JARVIS monitor canary: an intentional unhandled exception");
    }
}
