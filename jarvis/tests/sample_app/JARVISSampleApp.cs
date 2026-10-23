// A harmless test program for JARVIS's "inspect an error dialog" and "diagnose and fix an app error" checks (2026-10-16).
// It reads config.json next to itself. If the file is not valid JSON, or "port" is not a whole number, it shows a real
// Windows error dialog and writes the reason to app.log; otherwise it opens a small window saying it runs.
// Build: build.ps1 (the .NET Framework compiler that ships with Windows; nothing is installed).
using System;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class JARVISSampleApp {
    [STAThread]
    static int Main() {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string cfg = Path.Combine(dir, "config.json");
        string log = Path.Combine(dir, "app.log");
        int port;
        try {
            var data = (System.Collections.Generic.Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(cfg));
            if (!data.ContainsKey("port") || !(data["port"] is int)) throw new FormatException("\"port\" must be a whole number");
            port = (int)data["port"];
            if (data.ContainsKey("plugin") && !File.Exists(Path.Combine(dir, (string)data["plugin"]))) {   // a missing library
                string msg = "The code execution cannot proceed because " + (string)data["plugin"] + " was not found. Reinstalling the program may fix this problem.";
                File.AppendAllText(log, DateTime.Now.ToString("s") + " ERROR " + msg + Environment.NewLine);
                MessageBox.Show(msg, "JARVISSampleApp.exe - System Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 3;
            }
            string fail = null;
            if (data.ContainsKey("listen") && data["listen"] is bool && (bool)data["listen"]) {          // it serves on its port
                try { var l = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, port); l.Start(); l.Stop(); }
                catch (System.Net.Sockets.SocketException) { fail = "Port " + port + " is already in use by another program."; }
            }
            if (fail == null && data.ContainsKey("needs_mb") && data["needs_mb"] is int) {               // it needs free space
                var drive = new DriveInfo(Path.GetPathRoot(dir));
                long freeMb = drive.AvailableFreeSpace / (1024 * 1024);
                if (freeMb < (int)data["needs_mb"])
                    fail = "There is not enough space on the disk: it needs " + (int)data["needs_mb"] + " MB on " + drive.Name.TrimEnd('\\') + ", which has " + freeMb + " MB free.";
            }
            if (fail == null && data.ContainsKey("service_port") && data["service_port"] is int) {       // it needs a local service
                int sp = (int)data["service_port"];
                try { using (var c = new System.Net.Sockets.TcpClient()) { if (!c.ConnectAsync("127.0.0.1", sp).Wait(1500) || !c.Connected) throw new System.Net.Sockets.SocketException(10061); } }
                catch (Exception) { fail = "Could not connect to the JARVIS sample service at 127.0.0.1:" + sp + " (connection refused)."; }
            }
            if (fail != null) {
                File.AppendAllText(log, DateTime.Now.ToString("s") + " ERROR " + fail + Environment.NewLine);
                MessageBox.Show("JARVIS Sample App could not start.\n\n" + fail + "\n\nFile: " + cfg, "JARVIS Sample App - startup error",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 4;
            }
        } catch (Exception e) {
            string why = (e is FormatException || e is ArgumentException || e is InvalidOperationException)
                ? "config.json is not valid: " + e.Message : "config.json could not be read: " + e.GetType().Name;
            File.AppendAllText(log, DateTime.Now.ToString("s") + " ERROR " + why + Environment.NewLine);
            MessageBox.Show("JARVIS Sample App could not start.\n\n" + why + "\n\nFile: " + cfg,
                            "JARVIS Sample App - configuration error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
        File.AppendAllText(log, DateTime.Now.ToString("s") + " OK running on port " + port + Environment.NewLine);
        var f = new Form { Text = "JARVIS Sample App - running on port " + port, Width = 420, Height = 160 };
        f.Controls.Add(new Label { Text = "Running. Configuration is valid (port " + port + ").", AutoSize = true, Left = 20, Top = 30 });
        Application.Run(f);
        return 0;
    }
}
