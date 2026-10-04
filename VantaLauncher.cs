using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Desktop window for the local Vanta UI. WebView2 only. Never opens a browser.
class Program
{
    static Process _python;
    static IntPtr _job = IntPtr.Zero;
    static readonly object Gate = new object();
    static string _detected;
    static Mutex _mutex;
    static StreamWriter _log;

    [STAThread]
    static void Main()
    {
        bool createdNew;
        _mutex = new Mutex(true, "Local\\VantaDesktopWindow", out createdNew);
        if (!createdNew)
        {
            FocusExisting();
            return;
        }

        try
        {
            string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string url = "http://127.0.0.1:8765/";
            bool startedPython = false;
            if (!IsUp(url))
            {
                startedPython = StartPython(dir, url);
                DateTime deadline = DateTime.UtcNow.AddSeconds(25);
                while (DateTime.UtcNow < deadline)
                {
                    string candidate;
                    lock (Gate) { candidate = _detected; }
                    if (!string.IsNullOrEmpty(candidate)) url = candidate;
                    if (IsUp(url)) break;
                    if (_python != null && _python.HasExited) break;
                    Thread.Sleep(200);
                }
                lock (Gate)
                {
                    if (!string.IsNullOrEmpty(_detected)) url = _detected;
                }
            }

            if (!IsUp(url))
            {
                string detail = "Vanta did not start.";
                if (_python != null && _python.HasExited)
                    detail = "Vanta exited before it was ready (code " + _python.ExitCode + ").";
                MessageBox.Show(detail + "\r\n" + url, "VANTA");
                KillPython(dir);
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new VantaWindow(dir, url, startedPython));
        }
        catch (Exception ex)
        {
            try { MessageBox.Show(ex.ToString(), "VANTA"); } catch { }
        }
        finally
        {
            try { if (_log != null) _log.Dispose(); } catch { }
        }
    }

    static bool StartPython(string dir, string url)
    {
        string python = Path.Combine(dir, "python.exe");
        if (!File.Exists(python))
        {
            MessageBox.Show("python.exe was not found next to Vanta.exe.", "VANTA");
            return false;
        }
        CreateKillOnCloseJob();
        string data = Path.Combine(dir, ".vanta-data");
        Directory.CreateDirectory(data);
        _log = new StreamWriter(Path.Combine(data, "launcher-ui.log"), true, Encoding.UTF8);
        _log.AutoFlush = true;

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = python;
        psi.Arguments = "-m vanta";
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.EnvironmentVariables["VANTA_DATA_DIR"] = data;

        _python = new Process();
        _python.StartInfo = psi;
        _python.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null) return;
            try { _log.WriteLine(e.Data); } catch { }
            int listen = e.Data.IndexOf("listening on");
            int idx = e.Data.IndexOf("http://");
            if (listen >= 0 && idx >= 0)
            {
                lock (Gate) { _detected = e.Data.Substring(idx).Trim(); }
            }
        };
        _python.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null) return;
            try { _log.WriteLine(e.Data); } catch { }
        };
        _python.Start();
        try
        {
            if (_job != IntPtr.Zero)
                AssignProcessToJobObject(_job, _python.Handle);
        }
        catch { }
        _python.BeginOutputReadLine();
        _python.BeginErrorReadLine();
        return true;
    }

    internal static void KillPython(string dir)
    {
        try
        {
            if (_python != null && !_python.HasExited)
                _python.Kill();
        }
        catch { }
        try
        {
            string needle = dir.TrimEnd('\\');
            using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(
                "SELECT ProcessId, CommandLine, ExecutablePath FROM Win32_Process WHERE Name = 'python.exe'"))
            {
                foreach (ManagementObject mo in searcher.Get())
                {
                    string cmd = Convert.ToString(mo["CommandLine"]) ?? "";
                    string exe = Convert.ToString(mo["ExecutablePath"]) ?? "";
                    bool ours = cmd.IndexOf("-m vanta", StringComparison.OrdinalIgnoreCase) >= 0
                        && (exe.StartsWith(needle, StringComparison.OrdinalIgnoreCase)
                            || cmd.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0);
                    if (!ours) continue;
                    int pid = Convert.ToInt32(mo["ProcessId"]);
                    try { Process.GetProcessById(pid).Kill(); } catch { }
                }
            }
        }
        catch { }
    }

    static void FocusExisting()
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(8);
        while (DateTime.UtcNow < deadline)
        {
            IntPtr hwnd = FindWindow(null, "VANTA");
            if (hwnd != IntPtr.Zero)
            {
                ForceForeground(hwnd);
                return;
            }
            Thread.Sleep(200);
        }
    }

    static void ForceForeground(IntPtr hwnd)
    {
        uint foreThread = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
        uint thisThread = GetCurrentThreadId();
        if (foreThread != 0 && foreThread != thisThread)
            AttachThreadInput(foreThread, thisThread, true);
        ShowWindow(hwnd, 9);
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
        if (foreThread != 0 && foreThread != thisThread)
            AttachThreadInput(foreThread, thisThread, false);
    }

    static bool IsUp(string url)
    {
        try
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "GET";
            req.Timeout = 1500;
            req.ReadWriteTimeout = 1500;
            req.Proxy = null;
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            {
                int code = (int)resp.StatusCode;
                return code >= 200 && code < 500;
            }
        }
        catch
        {
            return false;
        }
    }

    static void CreateKillOnCloseJob()
    {
        _job = CreateJobObject(IntPtr.Zero, null);
        if (_job == IntPtr.Zero) return;
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr ptr = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(info, ptr, false);
            SetInformationJobObject(_job, JobObjectExtendedLimitInformation, ptr, (uint)length);
        }
        finally
        {
            Marshal.FreeHGlobal(ptr);
        }
    }

    const int JobObjectExtendedLimitInformation = 9;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
    [DllImport("kernel32.dll")]
    static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")]
    static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
}

class VantaWindow : Form
{
    readonly string _dir;
    readonly string _url;
    readonly bool _startedPython;
    WebView2 _web;
    Label _status;

    public VantaWindow(string dir, string url, bool startedPython)
    {
        _dir = dir;
        _url = url;
        _startedPython = startedPython;
        Text = "VANTA";
        try
        {
            string iconPath = Path.Combine(_dir, "assets", "vanta.ico");
            if (File.Exists(iconPath))
                Icon = new Icon(iconPath);
        }
        catch
        {
        }
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1480, 720);
        MinimumSize = new Size(1240, 620);
        BackColor = Color.FromArgb(12, 12, 16);
        _web = new WebView2();
        _web.Dock = DockStyle.Fill;
        _status = new Label();
        _status.Dock = DockStyle.Fill;
        _status.ForeColor = Color.White;
        _status.BackColor = Color.FromArgb(12, 12, 16);
        _status.TextAlign = ContentAlignment.MiddleCenter;
        _status.Text = "Opening Vanta...";
        Controls.Add(_web);
        Controls.Add(_status);
        _status.BringToFront();
        Shown += OnShown;
        FormClosing += OnClosing;
    }

    void OnShown(object sender, EventArgs e)
    {
        string userData = Path.Combine(_dir, ".vanta-data", "webview2");
        Directory.CreateDirectory(userData);
        try
        {
            CoreWebView2Environment.CreateAsync(null, userData).ContinueWith(delegate(System.Threading.Tasks.Task<CoreWebView2Environment> task)
            {
                BeginInvoke(new Action(delegate
                {
                    if (task.IsFaulted || task.IsCanceled)
                    {
                        _status.Text = "WebView2 failed to start. A browser was not opened.\r\n" + BaseError(task);
                        return;
                    }
                    _web.EnsureCoreWebView2Async(task.Result).ContinueWith(delegate(System.Threading.Tasks.Task ready)
                    {
                        BeginInvoke(new Action(delegate
                        {
                            if (ready.IsFaulted || ready.IsCanceled)
                            {
                                _status.Text = "WebView2 failed to start. A browser was not opened.\r\n" + BaseError(ready);
                                return;
                            }
                            _web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                            _web.CoreWebView2.WebMessageReceived += OnWebMessage;
                            _web.CoreWebView2.NavigationCompleted += delegate
                            {
                                _status.Visible = false;
                            };
                            _web.Source = new Uri(_url);
                        }));
                    });
                }));
            });
        }
        catch (Exception ex)
        {
            _status.Text = "WebView2 failed to start. A browser was not opened.\r\n" + ex.Message;
        }
    }

    static string BaseError(System.Threading.Tasks.Task task)
    {
        if (task.Exception == null) return "unknown error";
        return task.Exception.GetBaseException().Message;
    }

    void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string msg = "";
        try { msg = e.TryGetWebMessageAsString(); }
        catch { return; }
        if (msg == "browse-instance")
        {
            BeginInvoke(new Action(BrowseInstanceFolder));
            return;
        }
        if (msg == "browse-background")
            BeginInvoke(new Action(BrowseBackground));
    }

    void BrowseInstanceFolder()
    {
        using (FolderBrowserDialog dialog = new FolderBrowserDialog())
        {
            dialog.Description = "Select a Minecraft instance folder";
            dialog.ShowNewFolderButton = false;
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            if (_web.CoreWebView2 == null) return;
            string path = dialog.SelectedPath.Replace("\\", "\\\\").Replace("\"", "\\\"");
            _web.CoreWebView2.PostWebMessageAsJson("{\"type\":\"instance-folder\",\"path\":\"" + path + "\"}");
        }
    }

    void BrowseBackground()
    {
        using (OpenFileDialog dialog = new OpenFileDialog())
        {
            dialog.Title = "Choose a Vanta background image";
            dialog.Filter = "Images|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp";
            dialog.CheckFileExists = true;
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            if (_web.CoreWebView2 == null) return;
            string path = dialog.FileName.Replace("\\", "\\\\").Replace("\"", "\\\"");
            _web.CoreWebView2.PostWebMessageAsJson("{\"type\":\"background-file\",\"path\":\"" + path + "\"}");
        }
    }

    void OnClosing(object sender, FormClosingEventArgs e)
    {
        Program.KillPython(_dir);
    }
}
