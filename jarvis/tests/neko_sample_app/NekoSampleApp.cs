// A harmless test program for Neko's "inspect an error dialog" and "diagnose and fix an app error" checks (2026-10-16).
// It reads config.json next to itself. If the file is not valid JSON, or "port" is not a whole number, it shows a real
// Windows error dialog and writes the reason to app.log; otherwise it opens a small window saying it runs.
// Build: build.ps1 (the .NET Framework compiler that ships with Windows; nothing is installed).
using System;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class NekoSampleApp {
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
        } catch (Exception e) {
            string why = (e is FormatException || e is ArgumentException || e is InvalidOperationException)
                ? "config.json is not valid: " + e.Message : "config.json could not be read: " + e.GetType().Name;
            File.AppendAllText(log, DateTime.Now.ToString("s") + " ERROR " + why + Environment.NewLine);
            MessageBox.Show("Neko Sample App could not start.\n\n" + why + "\n\nFile: " + cfg,
                            "Neko Sample App - configuration error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
        File.AppendAllText(log, DateTime.Now.ToString("s") + " OK running on port " + port + Environment.NewLine);
        var f = new Form { Text = "Neko Sample App - running on port " + port, Width = 420, Height = 160 };
        f.Controls.Add(new Label { Text = "Running. Configuration is valid (port " + port + ").", AutoSize = true, Left = 20, Top = 30 });
        Application.Run(f);
        return 0;
    }
}
