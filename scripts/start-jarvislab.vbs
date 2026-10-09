' JARVIS: keep the WSL lab box (JarvisLab) running so its services (lab UIs, OpenJev, firewall) stay up.
' Started by the scheduled task "JarvisLab" at logon. Window style 0 = hidden (no console flash).
CreateObject("WScript.Shell").Run "wsl.exe -d JarvisLab --exec sleep infinity", 0, False
