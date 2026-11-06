using Anode.Core.Timeline;

namespace Anode.Daemon;

internal sealed record ActivityPreviewItem(string Id, string Path);

internal sealed class ActivityImagePreview : Form
{
    private readonly Panel _canvas = new() { Name = "ImagePreviewCanvas", Dock = DockStyle.Fill, AutoScroll = true, BackColor = Color.FromArgb(20, 23, 29) };
    private readonly PictureBox _picture = new() { Name = "ImagePreviewPicture", SizeMode = PictureBoxSizeMode.StretchImage };
    private readonly Label _scale = new() { Name = "ImageScale", AutoSize = true, Padding = new Padding(12, 8, 8, 0) };
    private readonly Label _position = new() { Name = "ImagePosition", AutoSize = true, Padding = new Padding(12, 8, 8, 0) };
    private readonly Button _previous = new() { Name = "ImagePrevious", Text = "← 上一张", AutoSize = true, Height = 30, Margin = new Padding(0, 0, 8, 0) };
    private readonly Button _next = new() { Name = "ImageNext", Text = "下一张 →", AutoSize = true, Height = 30, Margin = new Padding(0, 0, 8, 0) };
    private readonly Func<IReadOnlyList<ActivityPreviewItem>> _items;
    private readonly Action<string>? _onNavigate;
    private readonly HashSet<string> _unavailable = new(StringComparer.Ordinal);
    private string _currentId = "";
    private bool _fit = true;
    private double _zoom = 1;
    internal ActivityImagePreview(string path) : this(() => [new(path, path)], path, null) { }

    internal ActivityImagePreview(Func<IReadOnlyList<ActivityPreviewItem>> items, string selectedId, Action<string>? onNavigate)
    {
        _items = items;
        _onNavigate = onNavigate;
        Text = "Activity · 图片预览"; Name = "ActivityImagePreview";
        StartPosition = FormStartPosition.CenterParent; Size = new Size(1040, 740); MinimumSize = new Size(480, 320);
        // Two short rows keep navigation and zoom controls reachable at the minimum window width.
        var bar = new TableLayoutPanel { Dock = DockStyle.Top, Height = 82, ColumnCount = 1, RowCount = 2, Padding = new Padding(8, 3, 0, 0) };
        bar.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        bar.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        var navigation = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
        var zoomBar = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
        bar.Controls.Add(navigation, 0, 0); bar.Controls.Add(zoomBar, 0, 1);
        _previous.Click += (_, _) => Navigate(-1);
        _next.Click += (_, _) => Navigate(1);
        navigation.Controls.Add(_previous); navigation.Controls.Add(_next); navigation.Controls.Add(_position);
        Add("适合窗口", () => { _fit = true; UpdateZoom(); });
        Add("100%", () => { _fit = false; _zoom = 1; UpdateZoom(); });
        Add("−", () => { _fit = false; _zoom = Math.Max(.1, _zoom / 1.25); UpdateZoom(); });
        Add("＋", () => { _fit = false; _zoom = Math.Min(4, _zoom * 1.25); UpdateZoom(); });
        zoomBar.Controls.Add(_scale);
        _canvas.Controls.Add(_picture); Controls.Add(_canvas); Controls.Add(bar);
        _canvas.Resize += (_, _) => { if (_fit) UpdateZoom(); };
        KeyPreview = true; KeyDown += (_, e) => { if (e.KeyCode == Keys.Escape) Close(); };
        Shown += (_, _) => UpdateZoom();
        OpenImage(selectedId);
        void Add(string label, Action action)
        {
            var button = new Button { Text = label, AutoSize = false, Width = label is "−" or "＋" ? 38 : 75, Height = 30, Margin = new Padding(0, 0, 8, 0) };
            button.Click += (_, _) => action(); zoomBar.Controls.Add(button);
        }
    }

    internal void OpenImage(string id)
    {
        var item = _items().FirstOrDefault(item => item.Id == id)
            ?? throw new IOException("该截图已不在当前列表，或文件不可用。");
        if (id != _currentId || _picture.Image is null)
        {
            using var stream = new MemoryStream(File.ReadAllBytes(item.Path));
            using var source = Image.FromStream(stream);
            var image = new Bitmap(source);
            var old = _picture.Image;
            _picture.Image = image;
            old?.Dispose();
            _currentId = id;
            _unavailable.Remove(id);
            UpdateZoom();
        }
        UpdateNavigation();
    }

    private void Navigate(int step)
    {
        var items = Available();
        int current = items.FindIndex(item => item.Id == _currentId);
        if (current < 0) { UpdateNavigation(); return; }
        for (int i = current + step; i >= 0 && i < items.Count; i += step)
        {
            try
            {
                OpenImage(items[i].Id);
                _onNavigate?.Invoke(items[i].Id);
                return;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or OutOfMemoryException or System.Runtime.InteropServices.ExternalException)
            { _unavailable.Add(items[i].Id); }
        }
        UpdateNavigation();
    }

    private List<ActivityPreviewItem> Available() => _items().Where(item => !_unavailable.Contains(item.Id)).ToList();

    private void UpdateNavigation()
    {
        var items = Available();
        int index = items.FindIndex(item => item.Id == _currentId);
        _previous.Enabled = index > 0;
        _next.Enabled = index >= 0 && index < items.Count - 1;
        _position.Text = index >= 0 ? $"{index + 1} / {items.Count} · ← → 切换" : "当前截图不在筛选范围";
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == Keys.Left) { Navigate(-1); return true; }
        if (keyData == Keys.Right) { Navigate(1); return true; }
        return base.ProcessCmdKey(ref msg, keyData);
    }
    private void UpdateZoom()
    {
        if (_picture.Image is not { } image) return;
        if (_fit) _zoom = Math.Min(1, Math.Min(Math.Max(1, _canvas.ClientSize.Width - 4) / (double)image.Width, Math.Max(1, _canvas.ClientSize.Height - 4) / (double)image.Height));
        _picture.Size = new Size(Math.Max(1, (int)(image.Width * _zoom)), Math.Max(1, (int)(image.Height * _zoom)));
        _scale.Text = $"{_zoom:P0} · {image.Width} × {image.Height}";
    }
    protected override void Dispose(bool disposing) { if (disposing) { _picture.Image?.Dispose(); _picture.Image = null; } base.Dispose(disposing); }
}

internal sealed class ActivityCaptureSettingsDialog : Form
{
    private readonly NumericUpDown _budget = new() { Minimum = 1, Maximum = 65536, Width = 140, Name = "ScreenshotBudgetMiB", ThousandsSeparator = true };
    private readonly RadioButton _default = new() { Text = "使用默认目录", AutoSize = true, Name = "DefaultScreenshotFolder" };
    private readonly RadioButton _custom = new() { Text = "自定义目录", AutoSize = true };
    private readonly TextBox _folder = new() { Dock = DockStyle.Fill, Name = "ScreenshotFolder" };
    private readonly string _defaultFolder;
    internal long BudgetBytes => (long)_budget.Value * 1024 * 1024;
    internal string? CustomDirectory => _default.Checked ? null : ActivityCaptureSettings.NormalizeDirectory(_folder.Text.Trim());
    internal ActivityCaptureSettingsDialog(TimelineCaptureScheduler capture, string defaultFolder)
    {
        _defaultFolder = defaultFolder;
        Text = "Activity · 截图设置"; Name = "ActivityCaptureSettings";
        StartPosition = FormStartPosition.CenterParent; ClientSize = new Size(600, 310); MinimumSize = new Size(520, 345);
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), ColumnCount = 1, RowCount = 7 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 35)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        layout.Controls.Add(new Label { Text = "自动截图容量预算（MiB）", AutoSize = true });
        var budgetRow = new FlowLayoutPanel { Dock = DockStyle.Fill };
        _budget.Value = Math.Clamp((decimal)capture.BudgetBytes / 1048576, 1, 65536);
        budgetRow.Controls.Add(_budget); budgetRow.Controls.Add(new Label { Text = "默认 256 MiB；达到后暂停留图，文字日志继续", AutoSize = true, Padding = new Padding(8, 5, 0, 0) }); layout.Controls.Add(budgetRow);
        layout.Controls.Add(_default); layout.Controls.Add(_custom);
        var pathRow = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2 };
        pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); pathRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 84));
        var browse = new Button { Text = "选择…", Dock = DockStyle.Fill };
        browse.Click += (_, _) => { using var dialog = new FolderBrowserDialog { Description = "选择新截图的保存目录", InitialDirectory = _folder.Text, UseDescriptionForTitle = true }; if (dialog.ShowDialog(this) == DialogResult.OK) { _custom.Checked = true; _folder.Text = dialog.SelectedPath; } };
        pathRow.Controls.Add(_folder); pathRow.Controls.Add(browse); layout.Controls.Add(pathRow);
        layout.Controls.Add(new Label { Text = "保存后仅新截图使用所选目录；旧图片不移动，仍可预览和导出。\r\n预算只统计自动截图；导入图片和显式工具截图另计。", Dock = DockStyle.Fill });
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft };
        var save = new Button { Text = "保存", DialogResult = DialogResult.OK, AutoSize = true }; var cancel = new Button { Text = "取消", DialogResult = DialogResult.Cancel, AutoSize = true };
        var reset = new Button { Text = "恢复默认值", AutoSize = true, Name = "ResetScreenshotDefaults" };
        reset.Click += (_, _) => { _budget.Value = 256; _default.Checked = true; _folder.Text = _defaultFolder; };
        buttons.Controls.Add(save); buttons.Controls.Add(cancel); buttons.Controls.Add(reset); layout.Controls.Add(buttons);
        _folder.Text = capture.ScreenshotDirectory;
        _default.Checked = string.Equals(capture.ScreenshotDirectory, defaultFolder, StringComparison.OrdinalIgnoreCase); _custom.Checked = !_default.Checked;
        _default.CheckedChanged += (_, _) => { _folder.ReadOnly = _default.Checked; if (_default.Checked) _folder.Text = _defaultFolder; };
        _folder.ReadOnly = _default.Checked;
        AcceptButton = save; CancelButton = cancel; Controls.Add(layout);
    }
}

internal sealed class ActivityCleanupDialog : Form
{
    private readonly TextBox _passphrase = new() { Width = 150, UseSystemPasswordChar = true, Name = "CleanupPassphrase", AccessibleName = "清理确认口令" };
    internal string Passphrase => _passphrase.Text;
    internal bool FullListDisplayed { get; private set; }
    internal ActivityCleanupDialog(ActivityCleanupPlan plan)
    {
        Text = "Activity · 清理日志确认"; Name = "ActivityCleanupConfirmation";
        StartPosition = FormStartPosition.CenterParent; Size = new Size(850, 600); MinimumSize = new Size(600, 400);
        var heading = new Label { Dock = DockStyle.Top, Height = 96, Padding = new Padding(12), Text = $"将以下 {plan.Files.Length} 个文件（{plan.Bytes / 1048576.0:0.00} MiB）移入 Windows 回收站。\r\n已逐文件核对目录范围；只处理列出的文件，不删除文件夹。\r\n当前新日志、配置、备份与导出包保留。\r\n清理期间软件和文字记录继续运行，自动留图暂时暂停。" };
        var list = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false, Name = "CleanupFullFileList", Text = string.Join(Environment.NewLine, plan.Files.Select(f => $"{f.Path}  ({f.Bytes:N0} bytes)")) };
        var footer = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 64, Padding = new Padding(12) };
        var confirm = new Button { Name = "ConfirmActivityCleanup", Text = "移入回收站", Enabled = false, AutoSize = true, BackColor = Color.FromArgb(210, 215, 221), ForeColor = Color.DimGray, FlatStyle = FlatStyle.Flat, DialogResult = DialogResult.OK };
        footer.Controls.Add(new Label { Text = "查看完整清单后输入约定口令：", AutoSize = true, Padding = new Padding(0, 6, 0, 0) }); footer.Controls.Add(_passphrase); footer.Controls.Add(confirm);
        var cancel = new Button { Text = "取消", AutoSize = true, DialogResult = DialogResult.Cancel }; footer.Controls.Add(cancel);
        void UpdateConfirmation()
        {
            confirm.Enabled = ActivityCleanup.Confirmed(_passphrase.Text, FullListDisplayed);
            confirm.BackColor = confirm.Enabled ? Color.FromArgb(171, 32, 51) : Color.FromArgb(210, 215, 221);
            confirm.ForeColor = confirm.Enabled ? Color.White : Color.DimGray;
        }
        _passphrase.TextChanged += (_, _) => UpdateConfirmation();
        Shown += (_, _) => { FullListDisplayed = true; ActiveControl = _passphrase; UpdateConfirmation(); };
        CancelButton = cancel; Controls.Add(list); Controls.Add(footer); Controls.Add(heading);
    }
}
