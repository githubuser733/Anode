using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using Anode.Core.Native;
using Anode.Core.Timeline;
using System.Text.Json.Nodes;

namespace Anode.Cli;

/// <summary>
/// Design preview: renders the viewer, title bar included, in sample states to PNG files, never
/// connected. Each view is captured from a cloaked window placed far off-screen, owned by a
/// hidden window so it has no taskbar button, and unable to activate: Windows composes it without
/// ever displaying it. A view is skipped unless Windows confirms the cloak. No tray icon is created.
/// </summary>
internal static class ViewerPreview
{
    /// <param name="Startup">Keeps the size the window opens at, fitted to the seat, instead of a fixed preview size.</param>
    private sealed record Scene(string Name, string State, bool Control = false, bool Details = false,
        bool Seat = true, bool Narrow = false, string? Hover = null, bool Startup = false);

    private static readonly Scene[] Scenes =
    {
        new("ready", "ready"),
        new("startup", "ready", Startup: true),
        new("hover", "ready", Hover: "ControlSeat"),
        new("control", "ready", Control: true),
        new("connecting", "connecting", Seat: false),
        new("disconnected", "detached", Seat: false),
        new("sign-in-failed", "logon-error", Details: true, Seat: false),
        new("narrow", "ready", Narrow: true)
    };

    private static readonly Dictionary<string, string> Samples = new()
    {
        ["connecting"] = "Creating the seat...",
        ["ready"] = "Seat ready in session 2 at 1280x720. Programs you start here stay out of your way.",
        ["detached"] = "The viewer disconnected but the seat is still running. Press Reconnect in the viewer to watch it again.",
        ["logon-error"] = "Windows could not sign in the seat. Press Sign in… to use the Windows credential dialog."
    };

    public static int Run(string[] args)
    {
        ConsoleBridge.Attach();
        var options = new Args(args);
        string output = Path.GetFullPath(options.Value("out") ?? "viewer-preview");
        string? backdrop = options.Value("backdrop") ?? DefaultBackdrop();
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Directory.CreateDirectory(output);
        using var owner = new Form();
        _ = owner.Handle;
        foreach (var scene in Scenes) Render(scene, output, backdrop, owner, options.Flag("timeline"));
        if (options.Flag("timeline")) Render(new Scene("activity-filtered", "ready"), output, backdrop, owner, true);
        if (options.Flag("timeline")) RenderActivityDialogs(output, owner);
        RenderTrayMenu(output);
        Console.WriteLine(output);
        return 0;
    }

    private static void Render(Scene scene, string output, string? backdrop, Form owner, bool includeTimeline)
    {
        using var window = new Daemon.SeatWindow(new Daemon.SeatOptions(), trayIcon: false);
        using var timeline = includeTimeline ? new OperationTimeline(Path.Combine(output, "sample-" + scene.Name)) : null;
        if (timeline is not null)
        {
            timeline.TrackAsync(new JsonObject { ["op"] = "run", ["agentId"] = "preview", ["agentName"] = "Demo agent" },
                () => Task.FromResult(new JsonObject { ["ok"] = true })).GetAwaiter().GetResult();
            timeline.TrackAsync(new JsonObject { ["op"] = "input.click", ["agentId"] = "preview", ["agentName"] = "Demo agent", ["x"] = 640, ["y"] = 360 },
                () => Task.FromResult(new JsonObject { ["ok"] = true })).GetAwaiter().GetResult();
            timeline.Control(true);
            timeline.Human("input.click", "left click at 320,210");
            timeline.Human("input.text", "Typed 18 characters");
            timeline.Control(false);
            timeline.TrackAsync(new JsonObject { ["op"] = "ps.kill", ["agentId"] = "preview", ["agentName"] = "Demo agent" },
                () => Task.FromResult(new JsonObject { ["ok"] = false })).GetAwaiter().GetResult();
            using var sample = new Bitmap(320, 180);
            using (var graphics = Graphics.FromImage(sample))
            {
                graphics.Clear(Color.FromArgb(30, 50, 85));
                graphics.DrawString("Activity screenshot\nDesign preview", SystemFonts.MessageBoxFont!, Brushes.White, 30, 50);
            }
            using var encoded = new MemoryStream(); sample.Save(encoded, ImageFormat.Jpeg);
            timeline.TrackAsync(new JsonObject { ["op"] = "screenshot", ["agentId"] = "preview" },
                () => Task.FromResult(new JsonObject { ["ok"] = true, ["result"] = new JsonObject { ["data"] = Convert.ToBase64String(encoded.ToArray()) } })).GetAwaiter().GetResult();
            timeline.FlushAsync().GetAwaiter().GetResult();
        }
        using var capture = timeline is null ? null : new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(null));
        if (timeline is not null) window.AttachTimeline(timeline, recordHuman: false, capture: capture);
        window.AllowClose();
        window.SetSeatInfo(2, scene.State == "ready");
        if (scene.State == "ready") window.SetDesk("Claude Code", waiting: 1, known: true);
        window.SetStatus(Samples.GetValueOrDefault(scene.State, scene.State));
        window.SetHeadline(scene.State);
        window.ViewOnly = !scene.Control;
        // The ActiveX control shows a blank window of its own, even when hidden, until a
        // connection this preview never makes. It never gets a window here; a picture stands
        // in for the seat.
        window.Viewer.Visible = false;
        if (scene.Seat && backdrop is not null)
        {
            ShowBackdrop(window, backdrop);
            window.SetSeatPictureLive(true);
        }
        window.CreateControl();
        // The window fits itself to the seat when its handle is created; a scene's own size comes after.
        _ = window.Handle;
        if (!scene.Startup)
            window.Size = scene.Narrow
                ? new Size(window.MinimumSize.Width, window.LogicalToDeviceUnits(420))
                : new Size(window.LogicalToDeviceUnits(1100), window.LogicalToDeviceUnits(680));
        var toolbar = window.Controls.OfType<ToolStrip>().Single(s => s.Name == "SeatToolbar");
        if (scene.Details && toolbar.Items["ShowDetails"] is ToolStripButton details) details.Checked = true;
        window.PerformLayout();
        toolbar.PerformLayout();
        if (includeTimeline)
        {
            var panel = window.Controls.Find("OperationTimeline", true).Single();
            var seat = window.Controls.Find("SeatArea", true).Single();
            if (scene.Narrow && seat.ClientSize.Width < window.LogicalToDeviceUnits(320))
                throw new InvalidOperationException("TIMELINE_NARROW_LAYOUT_FAILED");
            if (Rectangle.Intersect(panel.Bounds, seat.Bounds) is { Width: > 0, Height: > 0 })
                throw new InvalidOperationException("TIMELINE_OVERLAPS_SEAT");
            var rows = (ListView)panel.Controls.Find("TimelineEntries", true).Single();
            if (scene.Name == "activity-filtered")
            {
                var level = panel.Controls.Find("操作级别筛选", true).OfType<ComboBox>().Single();
                level.SelectedItem = "高影响";
                if (rows.Items.Count != 1) throw new InvalidOperationException("ACTIVITY_PREVIEW_FILTER_FAILED");
            }
            _ = rows.Handle;
            var selected = rows.Items.Cast<ListViewItem>().FirstOrDefault(row => ((TimelineEntry)row.Tag!).Screenshot is not null) ?? rows.Items.Cast<ListViewItem>().FirstOrDefault();
            if (selected is not null) selected.Selected = true;
            if (scene.Narrow && rows.ClientSize.Height < 65) throw new InvalidOperationException("ACTIVITY_TABLE_HIDDEN_IN_COMPACT_LAYOUT");
        }
        if (scene.Hover is { } hover) toolbar.Items[hover]!.Select();
        Capture(window, owner, Path.Combine(output, scene.Name + ".png"));
    }

    private static void Capture(Form window, Form owner, string path)
    {
        IntPtr handle = window.Handle;
        int style = Native.GetWindowLong(handle, Native.GWL_EXSTYLE);
        Native.SetWindowLong(handle, Native.GWL_EXSTYLE, style | Native.WS_EX_NOACTIVATE);
        int cloak = 1;
        if (Native.DwmSetWindowAttribute(handle, Native.DWMWA_CLOAK, ref cloak, sizeof(int)) != 0
            || Native.DwmGetWindowAttribute(handle, Native.DWMWA_CLOAKED, out int cloaked, sizeof(int)) != 0 || cloaked == 0)
        {
            Console.Error.WriteLine($"Skipped {Path.GetFileName(path)}: Windows did not confirm the preview window is cloaked.");
            return;
        }
        // An owned window never gets a taskbar button.
        window.Owner = owner;
        window.Location = new Point(-20000, -20000);
        window.Show();
        try
        {
            for (int pass = 0; pass < 3; pass++)
            {
                window.Refresh();
                foreach (Control child in window.Controls) child.Refresh();
                Application.DoEvents();
                Native.DwmFlush();
            }
            if (Native.DwmGetWindowAttribute(handle, Native.DWMWA_EXTENDED_FRAME_BOUNDS, out Native.Rect frame, Marshal.SizeOf<Native.Rect>()) != 0) return;
            var bounds = window.Bounds;
            using var full = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb);
            using (var graphics = Graphics.FromImage(full))
            {
                IntPtr dc = graphics.GetHdc();
                try { Native.PrintWindow(handle, dc, Native.PW_RENDERFULLCONTENT); }
                finally { graphics.ReleaseHdc(dc); }
            }
            var visible = Rectangle.Intersect(new Rectangle(frame.Left - bounds.X, frame.Top - bounds.Y,
                frame.Right - frame.Left, frame.Bottom - frame.Top), new Rectangle(Point.Empty, bounds.Size));
            using var managed = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb);
            window.DrawToBitmap(managed, new Rectangle(Point.Empty, bounds.Size));
            // PrintWindow can omit text in cloaked native children. Use the managed client rendering,
            // retaining the actual Windows frame from PrintWindow. No viewer connection or input occurs.
            var clientOrigin = window.PointToScreen(Point.Empty);
            var client = new Rectangle(clientOrigin.X - bounds.X, clientOrigin.Y - bounds.Y, window.ClientSize.Width, window.ClientSize.Height);
            using (var graphics = Graphics.FromImage(full)) graphics.DrawImage(managed, client, client, GraphicsUnit.Pixel);
            using var image = full.Clone(visible, full.PixelFormat);
            using (var file = new FileStream(path, FileMode.CreateNew)) image.Save(file, ImageFormat.Png);
        }
        finally { window.Hide(); }
    }

    private static void RenderActivityDialogs(string output, Form owner)
    {
        using var timeline = new OperationTimeline(Path.Combine(output, "sample-settings"));
        using var capture = new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(null));
        using (var settings = new Daemon.ActivityCaptureSettingsDialog(capture, timeline.DirectoryPath)) Capture(settings, owner, Path.Combine(output, "capture-settings.png"));
        using var picture = new Bitmap(1280, 720);
        using (var graphics = Graphics.FromImage(picture))
        {
            graphics.Clear(Color.FromArgb(27, 42, 68));
            for (int x = 0; x < 1280; x += 80) graphics.DrawLine(Pens.SlateGray, x, 0, x, 720);
            for (int y = 0; y < 720; y += 80) graphics.DrawLine(Pens.SlateGray, 0, y, 1280, y);
            graphics.DrawString("Activity screenshot preview", SystemFonts.CaptionFont!, Brushes.White, 45, 45);
        }
        string path = Path.Combine(output, "image-preview-fixture.png");
        using (var stream = new FileStream(path, FileMode.CreateNew)) picture.Save(stream, ImageFormat.Png);
        using (var preview = new Daemon.ActivityImagePreview(path)) Capture(preview, owner, Path.Combine(output, "image-preview.png"));
        var plan = new ActivityCleanupPlan(0, [new(Path.Combine(timeline.DirectoryPath, "timeline-example.jsonl"), timeline.DirectoryPath, 2048, "fixture"), new(Path.Combine(timeline.DirectoryPath, "shot-example.jpg"), timeline.DirectoryPath, 91372, "fixture")]);
        using var cleanup = new Daemon.ActivityCleanupDialog(plan);
        Capture(cleanup, owner, Path.Combine(output, "cleanup-confirmation.png"));
    }

    private static void RenderTrayMenu(string output)
    {
        using var window = new Daemon.SeatWindow(new Daemon.SeatOptions(), trayIcon: false);
        var menu = window.TrayMenu;
        menu.CreateControl();
        // A menu sizes itself when shown; this one never is, so fit it around its laid-out items.
        menu.Size = new Size(1000, 1000);
        menu.PerformLayout();
        var items = menu.Items.Cast<ToolStripItem>().Select(i => i.Bounds).Aggregate(Rectangle.Union);
        menu.Size = new Size(items.Right + menu.Padding.Right, items.Bottom + menu.Padding.Bottom);
        menu.PerformLayout();
        menu.Items[0].Select();
        using var image = new Bitmap(menu.Width, menu.Height);
        menu.DrawToBitmap(image, new Rectangle(Point.Empty, menu.Size));
        image.Save(Path.Combine(output, "tray-menu.png"), ImageFormat.Png);
    }

    private static void ShowBackdrop(Daemon.SeatWindow window, string path) =>
        window.ShowStandIn(new PictureBox { SizeMode = PictureBoxSizeMode.StretchImage, Image = Image.FromFile(path) });

    private static string? DefaultBackdrop()
    {
        string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "Web", "Wallpaper", "Windows");
        return new[] { "img19.jpg", "img0.jpg" }.Select(name => Path.Combine(folder, name)).FirstOrDefault(File.Exists);
    }
}
