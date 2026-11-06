using Anode.Core.Timeline;

namespace Anode.Daemon;

internal sealed class TimelinePanel : UserControl
{
    private readonly ComboBox _filter = Box("Timeline actor filter", ["全部操作者", "Agent", "Human", "Client"]);
    private readonly ComboBox _category = Box("动作类别筛选", ["全部类别", .. ActivityPresentation.Categories]);
    private readonly ComboBox _level = Box("操作级别筛选", ["全部级别", .. ActivityPresentation.Levels]);
    private readonly ComboBox _status = Box("结果状态筛选", ["全部状态", .. ActivityPresentation.StatusGroups, "ok", "sent", "state", "running", "failed", "cancelled; effects unknown", "error; effects unknown"]);
    private readonly ComboBox _source = Box("活动来源筛选", ["全部来源", "现场记录", "导入记录"]);
    private readonly TextBox _search = new() { Dock = DockStyle.Fill, PlaceholderText = "搜索动作、操作者、摘要或结果", MaxLength = 512, AccessibleName = "搜索活动", Name = "ActivitySearch" };
    private readonly CheckBox _imagesOnly = new() { Text = "仅有截图", AutoSize = true, AccessibleName = "仅显示有截图的活动" };
    private readonly ToolStripComboBox _captureMode = new() { DropDownStyle = ComboBoxStyle.DropDownList, AutoSize = false, Width = 174, AccessibleName = "自动截图策略", ToolTipText = "选择哪些动作自动留图；频率与容量限制始终生效。" };
    private readonly ToolStrip _tools = Toolbar("ActivityArchiveTools");
    private readonly ToolStrip _captureTools = Toolbar("ActivityCaptureTools");
    private readonly ToolStripButton _settings = ToolButton("截图设置", "设置容量预算和新截图保存目录", "ScreenshotSettings");
    private readonly FlowLayoutPanel _legend = new() { Dock = DockStyle.Top, Height = 25, WrapContents = false, AccessibleName = "操作级别颜色说明" };
    private readonly Label _health = new() { Dock = DockStyle.Top, Height = 30, AutoEllipsis = true, Name = "ActivityHealth", Padding = new Padding(4, 5, 0, 0) };
    private readonly System.Windows.Forms.Timer _searchTimer = new() { Interval = 180 };
    private readonly System.Windows.Forms.Timer _refreshTimer = new() { Interval = 33 };
    private readonly ListView _rows = new() { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, HideSelection = false, MultiSelect = false };
    private readonly Dictionary<string, ListViewItem> _rowById = new(StringComparer.Ordinal);
    private readonly TextBox _detail = new() { Dock = DockStyle.Bottom, Multiline = true, ReadOnly = true, Height = 64, BorderStyle = BorderStyle.None };
    private readonly PictureBox _preview = new() { Dock = DockStyle.Bottom, Height = 88, SizeMode = PictureBoxSizeMode.Zoom, Visible = false, Cursor = Cursors.Hand, AccessibleName = "点击放大截图" };
    private readonly Label _footer = new() { Dock = DockStyle.Bottom, Height = 42, AutoEllipsis = true };
    private OperationTimeline? _timeline;
    private ActivityImagePreview? _imageWindow;
    private TimelineCaptureScheduler? _capture;
    private string? _previewKey;
    private OperationTimeline? _previewTimeline;
    private PreviewRequest? _previewRequest;
    private bool _previewLoading;
    private int _queued;
    private bool _busy, _syncingMode, _updatingRows, _rowColorsDirty;

    internal TimelinePanel()
    {
        Name = "OperationTimeline";
        AccessibleName = "Operation timeline";
        Dock = DockStyle.Right;
        Width = 520;
        Padding = new Padding(8);
        foreach (var box in new[] { _filter, _category, _level, _status, _source }) box.SelectedIndexChanged += (_, _) => RefreshEntries();
        _imagesOnly.CheckedChanged += (_, _) => RefreshEntries();
        _search.TextChanged += (_, _) => { _searchTimer.Stop(); _searchTimer.Start(); };
        _searchTimer.Tick += (_, _) => { _searchTimer.Stop(); RefreshEntries(); };
        // Notifications can arrive several times per capture. Never post one UI callback per state.
        _refreshTimer.Tick += (_, _) => { if (Visible && Volatile.Read(ref _queued) != 0) RefreshEntries(); };
        _refreshTimer.Start();
        var searchRow = new Panel { Dock = DockStyle.Top, Height = 30 };
        var clearSearch = new Button { Text = "×", Dock = DockStyle.Right, Width = 30, AccessibleName = "清空搜索" };
        clearSearch.Click += (_, _) => _search.Clear();
        _imagesOnly.Dock = DockStyle.Right; _imagesOnly.AutoSize = false; _imagesOnly.Width = 85;
        searchRow.Controls.Add(_search); searchRow.Controls.Add(_imagesOnly); searchRow.Controls.Add(clearSearch);
        var filters = new TableLayoutPanel { Dock = DockStyle.Top, Height = 60, ColumnCount = 3, RowCount = 2 };
        for (int i = 0; i < 3; i++) filters.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.333f));
        filters.Controls.Add(_filter, 0, 0); filters.Controls.Add(_category, 1, 0); filters.Controls.Add(_level, 2, 0);
        filters.Controls.Add(_status, 0, 1); filters.Controls.Add(_source, 1, 1);
        var reset = new Button { Text = "重置分类", Dock = DockStyle.Fill, Name = "ResetActivityFilters", AccessibleName = "重置分类筛选，保留搜索" };
        reset.Click += (_, _) => { foreach (var box in new[] { _filter, _category, _level, _status, _source }) box.SelectedIndex = 0; _imagesOnly.Checked = false; };
        filters.Controls.Add(reset, 2, 1);
        var import = ToolButton("↓ 导入", "导入 ZIP 记录与截图，不回放操作", "ImportActivity");
        import.Click += async (_, _) => await TransferAsync(importing: true);
        var export = new ToolStripDropDownButton("↑ 导出") { AccessibleName = "导出 Activity 归档", AutoSize = false, Size = new Size(88, 30), Margin = new Padding(3), ToolTipText = "导出全部历史或当前筛选，包含截图" };
        export.DropDownItems.Add("全部历史（含截图）", null, async (_, _) => await TransferAsync(importing: false));
        export.DropDownItems.Add("当前筛选（含截图）", null, async (_, _) => await TransferAsync(importing: false, filtered: true));
        var clear = ToolButton("清理日志…", "先预览完整清单并输入口令，再移入回收站", "ClearActivity");
        clear.Tag = "danger"; clear.Click += async (_, _) => await CleanupAsync();
        var checkpoint = ToolButton("◎ 手动截图", "记录当前关键画面，受频率和容量限制", "CaptureCheckpoint", 108);
        checkpoint.Click += (_, _) => _timeline?.Checkpoint();
        _captureMode.Items.AddRange(["自动截图：关闭", "自动截图：关键操作", "自动截图：全部（限频）"]);
        _captureMode.SelectedIndex = 1;
        _captureMode.SelectedIndexChanged += (_, _) => { if (_syncingMode) return; if (_capture is not null) _capture.Mode = (TimelineCaptureMode)_captureMode.SelectedIndex; RefreshEntries(); };
        _settings.Click += (_, _) => ConfigureCapture();
        _tools.Items.AddRange([new ToolStripLabel("归档") { Margin = new Padding(4, 0, 8, 0) }, import, export, new ToolStripSeparator(), clear]);
        _captureTools.Items.AddRange([checkpoint, new ToolStripSeparator(), _captureMode, _settings]);
        foreach (string operation in new[] { "screenshot", "input.click", "run", "ps.kill" })
        {
            var cue = ActivityPresentation.Cue(new(0, "legend", DateTimeOffset.UtcNow, "Client", "", operation, "", "ok", 0));
            _legend.Controls.Add(new Label { Text = "● " + cue.Level, AutoSize = true, ForeColor = Color.White, BackColor = cue.Color, Padding = new Padding(5, 2, 5, 2), Margin = new Padding(2) });
        }
        _rows.Name = "TimelineEntries";
        _rows.AccessibleName = "Agent and human activity";
        _rows.Columns.Add("Time", 68);
        _rows.Columns.Add("By", 62);
        _rows.Columns.Add("级别", 72);
        _rows.Columns.Add("Action", 170);
        _rows.Columns.Add("结果", 74);
        _rows.Columns.Add("图", 28);
        _rows.ShowItemToolTips = true;
        _rows.Resize += (_, _) => _rows.Columns[3].Width = Math.Max(130, _rows.ClientSize.Width - 320);
        _rows.ListViewItemSorter = new TimelineRowComparer();
        _rows.SelectedIndexChanged += (_, _) => { if (!_updatingRows) ShowEntry(); };
        _rows.DoubleClick += (_, _) => OpenPreview();
        _rows.KeyDown += (_, e) => { if (e.KeyCode == Keys.Enter) { OpenPreview(); e.Handled = true; } };
        _rows.MouseClick += (_, e) => { var hit = _rows.HitTest(e.Location); if (hit.Item is { } item && hit.SubItem == item.SubItems[5]) OpenPreview(); };
        _preview.Click += (_, _) => OpenPreview();
        Controls.Add(_rows);
        Controls.Add(_health); Controls.Add(_legend); Controls.Add(_captureTools); Controls.Add(_tools); Controls.Add(filters); Controls.Add(searchRow);
        Controls.Add(_detail);
        Controls.Add(_preview);
        Controls.Add(_footer);
        Resize += (_, _) => CompactLayout();
        VisibleChanged += (_, _) => { if (Visible && IsHandleCreated) RefreshEntries(); };
    }

    private void CompactLayout()
    {
        _detail.Visible = Height >= 480;
        _footer.Height = Height >= 480 ? 42 : 24;
        _tools.Height = _captureTools.Height = Height >= 480 ? 42 : 34;
        _legend.Height = Height >= 480 ? 25 : 18;
        _health.Height = Height >= 480 ? 30 : 22;
        _preview.Visible = _preview.Image is not null && Height >= 500;
    }

    private static ToolStrip Toolbar(string name) => new() { Name = name, Dock = DockStyle.Top, GripStyle = ToolStripGripStyle.Hidden, AutoSize = false, Height = 42, Padding = new Padding(0, 2, 0, 2), Renderer = new ActivityToolbarRenderer() };
    private static ToolStripButton ToolButton(string text, string tip, string name, int width = 90) => new(text) { Name = name, AccessibleName = text, ToolTipText = tip, AutoSize = false, Size = new Size(width, 30), Margin = new Padding(3), ForeColor = Color.White };

    private static ComboBox Box(string name, string[] choices)
    {
        var box = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill, AccessibleName = name, Name = name, DropDownWidth = 240 };
        box.Items.AddRange(choices); box.SelectedIndex = 0; return box;
    }

    internal void Bind(OperationTimeline timeline, TimelineCaptureScheduler? capture = null)
    {
        _imageWindow?.Close();
        _imageWindow = null;
        if (_timeline is not null) _timeline.Changed -= OnChanged;
        _timeline = timeline;
        _capture = capture;
        _captureMode.Enabled = capture is not null;
        _settings.Enabled = capture is not null;
        if (capture is not null) _captureMode.SelectedIndex = (int)capture.Mode;
        _timeline.Changed += OnChanged;
        RefreshEntries();
    }

    internal void SetColors(Color background, Color foreground, Color muted)
    {
        BackColor = _rows.BackColor = _detail.BackColor = _preview.BackColor = _search.BackColor = _tools.BackColor = _captureTools.BackColor = background;
        ForeColor = _rows.ForeColor = _detail.ForeColor = _search.ForeColor = foreground;
        foreach (var strip in new[] { _tools, _captureTools })
            foreach (ToolStripItem item in strip.Items)
                if (item is ToolStripComboBox combo) { combo.BackColor = SystemColors.Window; combo.ForeColor = SystemColors.WindowText; }
                else item.ForeColor = item is ToolStripLabel ? muted : Color.White;
        foreach (var box in new[] { _filter, _category, _level, _status, _source }) { box.BackColor = background; box.ForeColor = foreground; }
        _footer.ForeColor = muted;
        _rowColorsDirty = true;
        RefreshEntries();
    }

    private void OnChanged()
    {
        Interlocked.Exchange(ref _queued, 1);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        RefreshEntries();
    }

    private void RefreshEntries()
    {
        if (_timeline is null) return;
        Interlocked.Exchange(ref _queued, 0);
        if (_capture is not null && _captureMode.SelectedIndex != (int)_capture.Mode)
        { _syncingMode = true; try { _captureMode.SelectedIndex = (int)_capture.Mode; } finally { _syncingMode = false; } }
        var loaded = _timeline.Snapshot();
        var matched = FilteredEntries(loaded);
        var entries = matched.Take(300).ToArray();
        var visibleIds = entries.Select(e => e.Id).ToHashSet(StringComparer.Ordinal);
        _updatingRows = true;
        _rows.BeginUpdate();
        try
        {
            foreach (string id in _rowById.Keys.Where(id => !visibleIds.Contains(id)).ToArray())
            {
                var removed = _rowById[id];
                // Clear native/accessibility focus while the item still belongs to this control.
                removed.Focused = false;
                removed.Selected = false;
                _rows.Items.Remove(removed);
                _rowById.Remove(id);
            }
            var added = new List<ListViewItem>();
            foreach (var entry in entries)
            {
                if (!_rowById.TryGetValue(entry.Id, out var row))
                {
                    row = new ListViewItem(new string[6]) { UseItemStyleForSubItems = false };
                    _rowById.Add(entry.Id, row);
                    added.Add(row);
                }
                if (_rowColorsDirty || !Equals(row.Tag, entry)) UpdateRow(row, entry);
            }
            if (added.Count > 0) _rows.Items.AddRange(added.ToArray());
            if (!_rows.Items.Cast<ListViewItem>().Select(r => ((TimelineEntry)r.Tag!).Id).SequenceEqual(entries.Select(e => e.Id))) _rows.Sort();
            _rowColorsDirty = false;
        }
        finally { _rows.EndUpdate(); _updatingRows = false; }
        bool healthy = _timeline.WriteError is null && _timeline.NotQueued == 0;
        _health.Text = healthy ? $"● 日志正常 · 队列 {_timeline.PendingWrites} · 不阻塞软件操作" : $"● 日志异常 · {_timeline.NotQueued} 条未入队 · 软件操作继续";
        bool dark = BackColor.GetBrightness() < .5f;
        _health.ForeColor = healthy ? dark ? Color.FromArgb(54, 169, 110) : Color.FromArgb(0, 107, 60)
            : dark ? Color.FromArgb(220, 113, 48) : Color.FromArgb(154, 65, 0);
        _health.AccessibleDescription = _timeline.WriteError ?? "日志与截图失败不改变操作结果；耗时与资源消耗仍取决于实际环境。";
        _footer.Text = _timeline.WriteError is null && _timeline.NotQueued == 0
                ? $"显示 {entries.Length} / 匹配 {matched.Length} · 已载入 {loaded.Count}\r\n" + (_capture is null ? "既有工具截图 · 本地活动日志" : $"留图 {_capture.Captured} · 复用 {_capture.Reused} · 合并 {_capture.Coalesced} · {_capture.Bytes / 1048576.0:0.0}/{_capture.BudgetBytes / 1048576.0:0} MiB" + (_capture.Paused ? " · 清理预览中，留图暂停" : _capture.BudgetReached ? " · 预算已满" : $" · 失败 {_capture.Failed}") + (_capture.SettingsError is null ? "" : " · " + _capture.SettingsError))
            : $"Journal unavailable · {_timeline.NotQueued} unqueued entries";
        _footer.AccessibleDescription = _timeline.JournalPath + "\n截图目录：" + (_capture?.ScreenshotDirectory ?? _timeline.ImageDirectory);
        ShowEntry();
    }

    private void UpdateRow(ListViewItem row, TimelineEntry entry)
    {
        var cue = ActivityPresentation.Cue(entry);
        row.Tag = entry;
        row.ToolTipText = $"{entry.Summary}\n{entry.Operation} {entry.Action} · {entry.Outcome} · {entry.ElapsedMs} ms\n{entry.CaptureStatus}";
        string status = ActivityPresentation.Status(entry.Outcome);
        string[] text = [entry.At.ToLocalTime().ToString("HH:mm:ss"), entry.Actor + (entry.Source is null ? "" : " ↥"), "● " + cue.Level,
            entry.Summary, status, entry.Screenshot is null ? entry.CaptureStatus == "queued" ? "…" : "—" : "▣"];
        for (int i = 0; i < text.Length; i++) if (row.SubItems[i].Text != text[i]) row.SubItems[i].Text = text[i];
        row.SubItems[2].ForeColor = Color.White;
        row.SubItems[2].BackColor = cue.Color;
        row.SubItems[4].ForeColor = status is "失败" or "未确认" ? Color.White : _rows.ForeColor;
        row.SubItems[4].BackColor = status is "失败" or "未确认" ? Color.FromArgb(196, 0, 36) : _rows.BackColor;
    }

    private sealed class TimelineRowComparer : System.Collections.IComparer
    {
        public int Compare(object? x, object? y)
        {
            var a = (TimelineEntry)((ListViewItem)x!).Tag!;
            var b = (TimelineEntry)((ListViewItem)y!).Tag!;
            int time = b.At.CompareTo(a.At);
            return time == 0 ? b.Sequence.CompareTo(a.Sequence) : time;
        }
    }

    private TimelineEntry[] FilteredEntries(IReadOnlyList<TimelineEntry>? loaded = null) => (loaded ?? _timeline?.Snapshot() ?? []).Where(e => (_source.SelectedIndex != 1 || e.Source is null) && (_source.SelectedIndex != 2 || e.Source is not null) && ActivityPresentation.Matches(e, _search.Text,
        _filter.SelectedIndex > 0 ? _filter.Text : "", _category.SelectedIndex > 0 ? _category.Text : "",
        _level.SelectedIndex > 0 ? _level.Text : "", _status.SelectedIndex > 0 ? _status.Text : "", _imagesOnly.Checked))
        .OrderByDescending(e => e.At).ThenByDescending(e => e.Sequence).ToArray();

    private async Task TransferAsync(bool importing, bool filtered = false)
    {
        if (_timeline is null || _busy) return;
        string directory = Path.Combine(_timeline.DirectoryPath, "exports");
        Directory.CreateDirectory(directory);
        using FileDialog dialog = importing ? new OpenFileDialog { CheckFileExists = true, Title = "导入 Activity（仅合并记录，不回放）" }
            : new SaveFileDialog { Title = filtered ? "导出当前筛选结果" : "导出全部已保留历史", OverwritePrompt = false, FileName = $"anode-activity-{DateTime.Now:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}.zip" };
        dialog.Filter = "Anode Activity (*.zip)|*.zip"; dialog.InitialDirectory = directory;
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        var selected = filtered ? FilteredEntries() : null;
        string path = dialog.FileName;
        var timeline = _timeline;
        SetBusy(true);
        try
        {
            var result = await Task.Run(() => importing ? ActivityArchive.ImportAsync(timeline, path) : ActivityArchive.ExportAsync(timeline, path, selected));
            timeline.ArchiveEvent(importing ? "archive.import" : "archive.export", result.Entries);
            if (!IsDisposed && !Disposing) MessageBox.Show(this, $"{(importing ? "已导入" : "已导出")} {result.Entries} 条记录、{result.Images} 张截图。" + (result.Skipped > 0 ? $" 跳过 {result.Skipped} 条重复记录。" : ""), "Activity", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Exception ex) { if (!IsDisposed && !Disposing) MessageBox.Show(this, "操作未完成：" + ex.Message + "\r\n现有文件不会被覆盖；若产生未完成的新文件，会保留供检查。", "Activity", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        finally { if (!IsDisposed && !Disposing) { SetBusy(false); RefreshEntries(); } }
    }

    private void SetBusy(bool value) { _busy = value; _tools.Enabled = _captureTools.Enabled = !value; }

    private void ConfigureCapture()
    {
        if (_capture is null || _timeline is null || _busy) return;
        using var dialog = new ActivityCaptureSettingsDialog(_capture, _timeline.DirectoryPath);
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        try { _capture.Configure(dialog.BudgetBytes, dialog.CustomDirectory); RefreshEntries(); }
        catch (Exception ex) { MessageBox.Show(this, "截图设置未应用：" + ex.Message, "Activity", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
    }

    private async Task CleanupAsync()
    {
        if (_timeline is null || _busy) return;
        var timeline = _timeline; var capture = _capture; bool paused = false;
        SetBusy(true);
        try
        {
            if (capture is not null) { await capture.PauseForCleanupAsync(); paused = true; }
            var plan = await ActivityCleanup.PrepareAsync(timeline);
            if (IsDisposed || Disposing) return;
            using var dialog = new ActivityCleanupDialog(plan);
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            string passphrase = dialog.Passphrase; bool displayed = dialog.FullListDisplayed;
            var result = await WindowsActivityRecycleBin.OnStaAsync(() => ActivityCleanup.Execute(timeline, plan, passphrase, displayed));
            if (!IsDisposed && !Disposing) MessageBox.Show(this, result.Errors.Length == 0
                ? $"已将 {result.Recycled} 个文件移入 Windows 回收站，并核对原路径已不存在。"
                : $"已移入回收站 {result.Recycled} 个文件；清理已停止，其余文件保留。\r\n" + string.Join("\r\n", result.Errors),
                "Activity", MessageBoxButtons.OK, result.Errors.Length == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
        }
        catch (Exception ex) { if (!IsDisposed && !Disposing) MessageBox.Show(this, "清理未完成：" + ex.Message, "Activity", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        finally { if (paused) capture!.ResumeAfterCleanup(); if (!IsDisposed && !Disposing) { SetBusy(false); RefreshEntries(); } }
    }

    private void OpenPreview()
    {
        if (_rows.SelectedItems.Count == 0 || _rows.SelectedItems[0].Tag is not TimelineEntry entry || _timeline?.ScreenshotPath(entry) is null) return;
        try
        {
            if (_imageWindow is null || _imageWindow.IsDisposed)
            {
                var window = new ActivityImagePreview(PreviewItems, entry.Id, SelectPreviewEntry);
                _imageWindow = window;
                window.FormClosed += (_, _) => { if (ReferenceEquals(_imageWindow, window)) _imageWindow = null; };
                window.Show(FindForm());
            }
            else
            {
                _imageWindow.OpenImage(entry.Id);
                if (_imageWindow.WindowState == FormWindowState.Minimized) _imageWindow.WindowState = FormWindowState.Normal;
                _imageWindow.Activate();
            }
        }
        catch (Exception ex) { MessageBox.Show(this, "图片预览不可用：" + ex.Message, "Activity", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
    }

    private IReadOnlyList<ActivityPreviewItem> PreviewItems()
    {
        if (_timeline is null || IsDisposed || Disposing) return [];
        var items = new List<ActivityPreviewItem>();
        foreach (ListViewItem row in _rows.Items)
            if (row.Tag is TimelineEntry entry && _timeline.ScreenshotPath(entry) is { } path)
                items.Add(new(entry.Id, path));
        return items;
    }

    private void SelectPreviewEntry(string id)
    {
        if (!_rowById.TryGetValue(id, out var row) || row.ListView != _rows) return;
        row.Selected = true;
        row.EnsureVisible();
    }

    private void ShowEntry()
    {
        var entry = _rows.SelectedItems.Count > 0 ? _rows.SelectedItems[0].Tag as TimelineEntry : null;
        _detail.Text = entry is null ? "选择记录查看详情；点击缩略图或双击有图记录可放大。"
            : $"{entry.ActorName} · {entry.Operation} {entry.Action}\r\n{entry.Outcome} · {entry.ElapsedMs} ms · {(entry.Source is null ? "现场记录" : "导入记录（不执行）")}\r\n{entry.At.ToLocalTime():yyyy-MM-dd HH:mm:ss.fff} · 留图: {entry.CaptureStatus ?? "无"}" + (entry.CapturedAt is { } at ? $" @ {at.ToLocalTime():HH:mm:ss.fff}" : "") + (entry.Screenshot is null ? "" : "\r\n点击下方缩略图放大；预览可用左右键切换并缩放。");
        string? key = entry?.Screenshot is null ? null : entry.Id + "\0" + entry.ScreenshotDirectory + "\0" + entry.Screenshot;
        if (key == _previewKey && ReferenceEquals(_previewTimeline, _timeline) && _preview.Image is not null)
        {
            _preview.Visible = Height >= 500;
            return;
        }
        var old = _preview.Image;
        _preview.Image = null;
        old?.Dispose();
        _preview.Visible = false;
        _previewKey = key;
        _previewTimeline = _timeline;
        _previewRequest = key is null || entry is null || _timeline is null ? null : new(_timeline, entry, key);
        if (_previewRequest is not null) _ = LoadPreviewAsync();
    }

    private sealed record PreviewRequest(OperationTimeline Timeline, TimelineEntry Entry, string Key);

    private async Task LoadPreviewAsync()
    {
        // Only one decode runs at a time; rapid selection changes replace the pending request.
        if (_previewLoading) return;
        _previewLoading = true;
        try
        {
            while (!IsDisposed && !Disposing && _previewRequest is { } request)
            {
                _previewRequest = null;
                var result = await Task.Run(() => ReadPreview(request));
                if (IsDisposed || Disposing || request.Key != _previewKey || !ReferenceEquals(request.Timeline, _previewTimeline))
                { result.Image?.Dispose(); continue; }
                if (result.Image is not null)
                {
                    _preview.Image = result.Image;
                    _preview.Visible = Height >= 500;
                    if (_previewRequest?.Key == request.Key) _previewRequest = null;
                }
                else if (result.Failed) _detail.AppendText("\r\nPreview unavailable");
            }
        }
        finally { _previewLoading = false; }
    }

    private static (Bitmap? Image, bool Failed) ReadPreview(PreviewRequest request)
    {
        Bitmap? reduced = null;
        try
        {
            if (request.Timeline.ScreenshotPath(request.Entry) is not { } path) return (null, false);
            using var stream = new MemoryStream(File.ReadAllBytes(path));
            using var image = Image.FromStream(stream);
            float scale = Math.Min(1f, Math.Min(720f / image.Width, 400f / image.Height));
            reduced = new Bitmap(Math.Max(1, (int)(image.Width * scale)), Math.Max(1, (int)(image.Height * scale)));
            using (var graphics = Graphics.FromImage(reduced)) graphics.DrawImage(image, 0, 0, reduced.Width, reduced.Height);
            return (reduced, false);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or OutOfMemoryException or System.Runtime.InteropServices.ExternalException)
        { reduced?.Dispose(); return (null, true); }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _imageWindow?.Close();
            _imageWindow = null;
            if (_timeline is not null) _timeline.Changed -= OnChanged;
            _previewRequest = null;
            _preview.Image?.Dispose();
            _preview.Image = null;
            _searchTimer.Dispose();
            _refreshTimer.Dispose();
        }
        base.Dispose(disposing);
    }
}

internal sealed class ActivityToolbarRenderer : ToolStripProfessionalRenderer
{
    protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e) { using var brush = new SolidBrush(e.ToolStrip.BackColor); e.Graphics.FillRectangle(brush, e.AffectedBounds); }
    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) { }
    protected override void OnRenderButtonBackground(ToolStripItemRenderEventArgs e)
    {
        bool danger = Equals(e.Item.Tag, "danger");
        Color fill = danger ? Color.FromArgb(92, 39, 48) : Color.FromArgb(43, 62, 87);
        if (e.Item.Selected || e.Item.Pressed) fill = danger ? Color.FromArgb(130, 45, 62) : Color.FromArgb(57, 87, 124);
        using var brush = new SolidBrush(fill); using var pen = new Pen(danger ? Color.FromArgb(183, 77, 96) : Color.FromArgb(94, 124, 164));
        var bounds = new Rectangle(0, 1, e.Item.Width - 1, e.Item.Height - 3);
        e.Graphics.FillRectangle(brush, bounds); e.Graphics.DrawRectangle(pen, bounds);
    }
    protected override void OnRenderDropDownButtonBackground(ToolStripItemRenderEventArgs e) => OnRenderButtonBackground(e);
    protected override void OnRenderArrow(ToolStripArrowRenderEventArgs e) { if (e.Item?.Owner is not ToolStripDropDown) e.ArrowColor = Color.White; base.OnRenderArrow(e); }
    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
    {
        if (e.ToolStrip is not ToolStripDropDown && e.Item is not ToolStripLabel) e.TextColor = e.Item.Enabled ? Color.White : Color.FromArgb(169, 179, 191);
        base.OnRenderItemText(e);
    }
}
