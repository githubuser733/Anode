using System.Drawing.Imaging;
using System.Reflection;
using System.Text.Json;
using Anode.Core.Timeline;
using Anode.Daemon;

namespace Anode.Cli;

// This opt-in check opens only synthetic local forms. Evidence is retained and no seat is created.
internal static class ActivityPreviewChecks
{
    internal static int Run(string[] args)
    {
        ConsoleBridge.Attach();
        try
        {
            string root = new Args(args).Value("out") ?? throw new ArgumentException("--out required");
            if (!Path.IsPathFullyQualified(root) || Directory.Exists(root) || File.Exists(root))
                throw new ArgumentException("Use a new absolute evidence directory.");
            Directory.CreateDirectory(root);
            var checks = new List<string>();
            void Check(bool result, string name)
            {
                if (!result) throw new InvalidOperationException(name);
                checks.Add(name);
            }

            using var timeline = new OperationTimeline(Path.Combine(root, "timeline"));
            void Add(string label, Color? color, bool corrupt = false)
            {
                timeline.Human("input.click", label);
                if (color is null && !corrupt) return;
                var id = timeline.Snapshot().Last().Id;
                byte[] bytes;
                if (corrupt) bytes = [1, 2, 3, 4];
                else
                {
                    using var bitmap = new Bitmap(32, 18);
                    using (var g = Graphics.FromImage(bitmap)) g.Clear(color!.Value);
                    using var stream = new MemoryStream(); bitmap.Save(stream, ImageFormat.Png); bytes = stream.ToArray();
                }
                if (!timeline.CaptureState(id, "saved", bytes, DateTimeOffset.UtcNow))
                    throw new InvalidOperationException("Fixture screenshot could not be queued.");
            }
            Add("red", Color.Red);
            Add("corrupt", null, true);
            Add("without screenshot", null);
            Add("green", Color.Lime);
            Add("blue", Color.Blue);
            Check(timeline.FlushAsync().GetAwaiter().GetResult(), "fixture journal flushed");

            using var host = new Form { Name = "ActivityPreviewTestHost", Text = "Anode screenshot preview test", Size = new Size(700, 700), ShowInTaskbar = false };
            using var panel = new TimelinePanel { Dock = DockStyle.Fill };
            host.Controls.Add(panel); panel.Bind(timeline); host.Show(); Application.DoEvents();
            var rows = (ListView)panel.Controls.Find("TimelineEntries", true).Single();
            var open = typeof(TimelinePanel).GetMethod("OpenPreview", BindingFlags.NonPublic | BindingFlags.Instance)!;
            void Open(string summary)
            {
                var row = rows.Items.Cast<ListViewItem>().Single(r => ((TimelineEntry)r.Tag!).Summary == summary);
                foreach (ListViewItem selected in rows.SelectedItems.Cast<ListViewItem>().ToArray()) selected.Selected = false;
                row.Selected = true; Application.DoEvents(); open.Invoke(panel, null); Application.DoEvents();
            }
            Color Pixel(ActivityImagePreview viewer) => ((Bitmap)((PictureBox)viewer.Controls.Find("ImagePreviewPicture", true).Single()).Image!).GetPixel(10, 10);
            bool Arrow(ActivityImagePreview viewer, Keys key)
            {
                var method = typeof(ActivityImagePreview).GetMethod("ProcessCmdKey", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                    ?? throw new InvalidOperationException("Preview does not handle arrow keys.");
                object[] values = [Message.Create(viewer.Handle, 0x100, (IntPtr)(int)key, IntPtr.Zero), key];
                return (bool)method.Invoke(viewer, values)!;
            }
            if (args.Contains("--interactive"))
            {
                Open("blue");
                using var expiry = new System.Windows.Forms.Timer { Interval = 120000 };
                expiry.Tick += (_, _) => host.Close();
                expiry.Start();
                Application.Run(host);
                return 0;
            }
            try
            {
                Open("blue");
                var first = Application.OpenForms.OfType<ActivityImagePreview>().Single();
                Check(Pixel(first).B > 200, "first selected screenshot is displayed");
                Open("green");
                var openForms = Application.OpenForms.OfType<ActivityImagePreview>().ToArray();
                Check(openForms.Length == 1 && ReferenceEquals(first, openForms[0]), "opening another screenshot reused the same preview window");
                Check(Pixel(first).G > 200 && Pixel(first).R < 20, "reused window displays the second screenshot");
                Check(Arrow(first, Keys.Right), "right arrow handled by preview");
                Check(Pixel(first).R > 200 && Pixel(first).G < 20, "right arrow skips unavailable screenshots in displayed order");
                Check(Arrow(first, Keys.Left), "left arrow handled by preview");
                Check(Pixel(first).G > 200, "left arrow returns to previous screenshot");
                var next = (Button)first.Controls.Find("ImageNext", true).Single();
                next.PerformClick(); Application.DoEvents();
                Check(Pixel(first).R > 200, "next button follows the same order as keyboard");
                Check(!next.Enabled, "next button is disabled at the end");
                var previous = (Button)first.Controls.Find("ImagePrevious", true).Single();
                previous.PerformClick(); Application.DoEvents();
                Check(Pixel(first).G > 200, "previous button returns to prior screenshot");
                first.Size = first.MinimumSize;
                first.PerformLayout(); Application.DoEvents();
                Check(next.Visible && next.Right <= next.Parent!.ClientSize.Width && previous.Visible,
                    "navigation controls remain visible at minimum width");
                var zoomButton = first.Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<FlowLayoutPanel>()
                    .SelectMany(box => box.Controls.OfType<Button>()).Single(b => b.Text == "100%");
                Check(zoomButton.Visible && zoomButton.Right <= zoomButton.Parent!.ClientSize.Width,
                    "zoom controls remain visible at minimum width");
                var scale = (Label)first.Controls.Find("ImageScale", true).Single();
                Check(scale.Visible && scale.Right <= scale.Parent!.ClientSize.Width,
                    "image scale remains visible at minimum width");
                first.Close(); Application.DoEvents();
                Open("blue");
                Check(Application.OpenForms.OfType<ActivityImagePreview>().Count() == 1, "closed preview can be opened again");
            }
            finally
            {
                foreach (var form in Application.OpenForms.OfType<ActivityImagePreview>().ToArray()) form.Close();
                host.Close();
            }
            using var report = new FileStream(Path.Combine(root, "result.json"), FileMode.CreateNew);
            JsonSerializer.Serialize(report, new { result = "ACTIVITY_PREVIEW_CHECK_OK", checks, liveSeatStarted = false });
            Console.WriteLine($"ACTIVITY_PREVIEW_CHECK_OK checks={checks.Count} liveSeatStarted=false");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
