using System.Diagnostics;
using System.Drawing.Imaging;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Anode.Core.Timeline;
using Anode.Daemon;

namespace Anode.Cli;

/// <summary>Retained fixtures only: never creates a seat or sends desktop input.</summary>
internal static class ActivityResponsivenessChecks
{
    internal static int Run(string root)
    {
        var failures = new List<string>();
        int checks = 0;
        void Check(bool value, string message)
        {
            checks++;
            if (!value) failures.Add(message);
        }
        using var timeline = new OperationTimeline(Path.Combine(root, "activity-responsiveness"));
        for (int i = 0; i < 500; i++) timeline.Human("input.click", "Fixture " + i);
        Check(timeline.FlushAsync().GetAwaiter().GetResult(), "Fixture journal did not flush.");
        using var panel = new TimelinePanel { Size = new Size(520, 720) };
        panel.Bind(timeline);
        _ = panel.Handle;
        var rows = (ListView)panel.Controls.Find("TimelineEntries", true).Single();
        _ = rows.Handle;
        var preview = panel.Controls.OfType<PictureBox>().Single();
        Check(rows.Items.Count == 300, "The visible activity limit changed.");

        ListViewItem? Row(string id) => rows.Items.Cast<ListViewItem>().FirstOrDefault(r => ((TimelineEntry)r.Tag!).Id == id);
        foreach (string actor in new[] { "Human", "Agent", "Client" })
        {
            if (actor == "Human") timeline.Human("input.click", actor + " fixture");
            else
            {
                var request = new JsonObject { ["op"] = "input.click" };
                if (actor == "Agent") request["agentId"] = "responsiveness-fixture";
                timeline.TrackAsync(request, () => Task.FromResult(new JsonObject { ["ok"] = true })).GetAwaiter().GetResult();
            }
            string id = timeline.Snapshot().Last().Id;
            PumpUntil(() => Row(id) is not null);
            var selected = Row(id)!;
            selected.Selected = selected.Focused = true;
            int selectionChanges = 0;
            ListViewItemSelectionChangedEventHandler changed = (_, _) => selectionChanges++;
            rows.ItemSelectionChanged += changed;
            Check(timeline.CaptureState(id, "queued"), actor + " capture status was not queued.");
            PumpUntil(() => Row(id)?.Tag is TimelineEntry { CaptureStatus: "queued" });
            Check(ReferenceEquals(Row(id), selected) && selected.ListView == rows,
                actor + " screenshot status detached and recreated the existing activity row.");
            Check(selectionChanges == 0 && selected.Selected && ReferenceEquals(rows.FocusedItem, selected),
                actor + " screenshot status churned selection or lost the focused activity.");
            rows.ItemSelectionChanged -= changed;
            try { _ = rows.AccessibilityObject.GetFocused(); Check(true, ""); }
            catch (InvalidOperationException ex) { Check(false, actor + " accessibility focus failed: " + ex.Message); }

            using var bitmap = new Bitmap(1920, 1080);
            using (var graphics = Graphics.FromImage(bitmap)) graphics.Clear(actor == "Agent" ? Color.Blue : Color.Red);
            using var bytes = new MemoryStream();
            bitmap.Save(bytes, ImageFormat.Jpeg);
            Check(timeline.CaptureState(id, "saved", bytes.ToArray(), DateTimeOffset.UtcNow), actor + " image was not queued.");
            Check(timeline.FlushAsync().GetAwaiter().GetResult(), actor + " image did not flush.");
            PumpUntil(() => Row(id)?.Tag is TimelineEntry { Screenshot: not null });
            Check(ReferenceEquals(Row(id), selected) && selected.ListView == rows,
                actor + " completed screenshot replaced the activity row.");
        }

        // Selecting an already saved screenshot must return before any thumbnail is decoded.
        foreach (ListViewItem item in rows.SelectedItems) item.Selected = false;
        var imageRow = rows.Items.Cast<ListViewItem>().First(r => ((TimelineEntry)r.Tag!).Screenshot is not null);
        imageRow.Selected = true;
        Check(preview.Image is null, "Selecting a saved screenshot synchronously read/decoded the thumbnail on the UI thread.");
        PumpUntil(() => preview.Image is not null);
        Check(preview.Image!.Width <= 720 && preview.Image.Height <= 400, "Preview did not preserve the bounded thumbnail size.");

        // Preview decoding awaits background work; its continuation must return to this UI thread.
        int uiThread = Environment.CurrentManagedThreadId;
        var continueOnUi = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        async Task<int> ResumedThreadAsync()
        {
            await continueOnUi.Task;
            return Environment.CurrentManagedThreadId;
        }
        var resumedThread = ResumedThreadAsync();
        continueOnUi.SetResult(true);
        PumpUntil(() => resumedThread.IsCompleted);
        int continuationThread = resumedThread.GetAwaiter().GetResult();
        Check(continuationThread == uiThread,
            "ACTIVITY_UI_CONTEXT_LOST: background work resumed outside the fixture UI thread.");

        var imageRows = rows.Items.Cast<ListViewItem>().Where(r => ((TimelineEntry)r.Tag!).Screenshot is not null).ToArray();
        for (int i = 0; i < 30; i++)
        {
            foreach (var item in rows.SelectedItems.Cast<ListViewItem>().ToArray()) item.Selected = false;
            imageRows[i % imageRows.Length].Selected = true;
        }
        foreach (var item in rows.SelectedItems.Cast<ListViewItem>().ToArray()) item.Selected = false;
        imageRows.Single(r => ((TimelineEntry)r.Tag!).Actor == "Agent").Selected = true;
        PumpUntil(() => preview.Image is not null);
        var pixel = ((Bitmap)preview.Image!).GetPixel(preview.Image.Width / 2, preview.Image.Height / 2);
        Check(pixel.B > 200 && pixel.R < 30, "A stale thumbnail replaced the latest selected screenshot.");

        var filter = panel.Controls.Find("Timeline actor filter", true).OfType<ComboBox>().Single();
        rows.SelectedItems[0].Focused = true;
        filter.SelectedIndex = 2;
        try { _ = rows.AccessibilityObject.GetFocused(); Check(rows.FocusedItem is null || rows.FocusedItem.ListView == rows, "Filtering left focus attached to an excluded item."); }
        catch (InvalidOperationException ex) { Check(false, "Focus lookup after filtering failed: " + ex.Message); }
        filter.SelectedIndex = 0;

        panel.Visible = false;
        var beforeHidden = rows.Items.Cast<ListViewItem>().ToArray();
        for (int i = 0; i < 50; i++) timeline.Human("input.click", "Hidden fixture " + i);
        PumpFor(220);
        Check(beforeHidden.SequenceEqual(rows.Items.Cast<ListViewItem>()), "A hidden activity panel kept rebuilding in response to background captures.");
        panel.Visible = true;
        string newest = timeline.Snapshot().Last().Id;
        PumpUntil(() => Row(newest) is not null);
        Check(rows.Items.Count == 300, "Showing the panel lost the bounded latest activity view.");

        var evicted = rows.Items[rows.Items.Count - 1];
        evicted.Selected = evicted.Focused = true;
        timeline.Human("input.click", "Eviction fixture");
        newest = timeline.Snapshot().Last().Id;
        PumpUntil(() => Row(newest) is not null);
        Check(evicted.ListView is null && !evicted.Focused, "An evicted row retained native focus.");
        try { _ = rows.AccessibilityObject.GetFocused(); Check(rows.FocusedItem is null || rows.FocusedItem.ListView == rows, "Accessibility focus referenced a detached row."); }
        catch (InvalidOperationException ex) { Check(false, "Focus lookup after eviction failed: " + ex.Message); }

        // Measure the same 300-row screenshot-state refresh before and after the repair.
        var refresh = typeof(TimelinePanel).GetMethod("RefreshEntries", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var samples = new List<double>();
        for (int i = 0; i < 30; i++)
        {
            timeline.CaptureState(newest, "fixture state " + i);
            var clock = Stopwatch.StartNew();
            refresh.Invoke(panel, null);
            samples.Add(clock.Elapsed.TotalMilliseconds);
        }
        samples.Sort();
        int thumbnailErrors = 0;
        ThreadExceptionEventHandler onThreadException = (_, _) => thumbnailErrors++;
        Application.ThreadException += onThreadException;
        try
        {
            using var closingPanel = new TimelinePanel { Size = new Size(520, 720) };
            closingPanel.Bind(timeline);
            _ = closingPanel.Handle;
            var closingRows = (ListView)closingPanel.Controls.Find("TimelineEntries", true).Single();
            _ = closingRows.Handle;
            // Image fixtures are still in the 500-entry model even after leaving the 300-row view.
            var closingFilter = closingPanel.Controls.Find("Timeline actor filter", true).OfType<ComboBox>().Single();
            closingFilter.SelectedIndex = 1;
            closingRows.Items[0].Selected = true;
            closingPanel.Dispose();
            PumpFor(100);
            Check(thumbnailErrors == 0, "Closing a panel during a thumbnail decode raised an unhandled UI exception.");
        }
        finally { Application.ThreadException -= onThreadException; }
        using var report = new FileStream(Path.Combine(root, "activity-responsiveness-result.json"), FileMode.CreateNew);
        JsonSerializer.Serialize(report, new { checks, failures, visibleRows = rows.Items.Count, uiThread, continuationThread,
            refreshMedianMs = samples[samples.Count / 2], refreshP95Ms = samples[(int)(samples.Count * .95)], inputSent = false, liveSeatStarted = false });
        if (failures.Count != 0) throw new InvalidOperationException(string.Join(Environment.NewLine, failures));
        return checks;
    }

    private static void PumpUntil(Func<bool> ready)
    {
        var clock = Stopwatch.StartNew();
        while (!ready() && clock.Elapsed < TimeSpan.FromSeconds(5)) { Application.DoEvents(); Thread.Sleep(2); }
        if (!ready()) throw new TimeoutException("Activity fixture did not receive the expected UI update within five seconds.");
    }

    private static void PumpFor(int milliseconds)
    {
        var clock = Stopwatch.StartNew();
        while (clock.ElapsedMilliseconds < milliseconds) { Application.DoEvents(); Thread.Sleep(2); }
    }
}
