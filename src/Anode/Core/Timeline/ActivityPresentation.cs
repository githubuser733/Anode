namespace Anode.Core.Timeline;

internal sealed record ActivityCue(string Level, string Category, Color Color);

/// <summary>Levels describe the known interface, not the meaning of a button or arbitrary command.</summary>
internal static class ActivityPresentation
{
    public static readonly string[] Levels = ["观察", "交互", "变更", "高影响"];
    public static readonly string[] Categories = ["鼠标", "键盘", "窗口/UI", "观察", "控制权", "应用", "进程/命令", "会话/显示", "归档", "其他"];
    public static readonly string[] StatusGroups = ["完成", "进行中", "失败", "未确认", "状态"];

    public static ActivityCue Cue(TimelineEntry entry)
    {
        string op = entry.Operation;
        string category = op.StartsWith("control.") || op is "lease" or "seat.control" ? "控制权"
            : op.StartsWith("archive.") ? "归档"
            : op.StartsWith("input.key") || op is "input.text" or "input.type" ? "键盘"
            : op.StartsWith("input.") ? "鼠标"
            : op.StartsWith("desktop.") ? "窗口/UI"
            : op is "screenshot" or "checkpoint" ? "观察"
            : op is "run" or "browser.open" or "steam.launch" ? "应用"
            : op.StartsWith("exec.") || op.StartsWith("ps.") ? "进程/命令"
            : op.StartsWith("seat.") || op is "display.set" or "quit" or "kill" ? "会话/显示" : "其他";
        int level = op is "ps.kill" or "exec.cancel" or "seat.stop" or "quit" or "kill"
                || op == "desktop.window" && entry.Action == "close" ? 3
            : category is "应用" or "进程/命令" or "会话/显示" or "归档" or "控制权" ? 2
            : op is "screenshot" or "checkpoint" or "desktop.observe" or "desktop.windows" or "desktop.wait" ? 0 : 1;
        Color color = level switch { 0 => Color.FromArgb(0, 80, 212), 1 => Color.FromArgb(0, 107, 60), 2 => Color.FromArgb(176, 79, 0), _ => Color.FromArgb(196, 0, 36) };
        return new(Levels[level], category, color);
    }

    public static string Status(string outcome) => outcome == "running" ? "进行中"
        : outcome is "failed" || outcome.StartsWith("error", StringComparison.Ordinal) ? "失败"
        : outcome.Contains("unknown", StringComparison.Ordinal) || outcome.StartsWith("interrupted", StringComparison.Ordinal) ? "未确认"
        : outcome is "state" ? "状态" : outcome is "ok" or "sent" ? "完成" : "未确认";

    public static bool Matches(TimelineEntry entry, string search = "", string actor = "", string category = "", string level = "", string status = "", bool imagesOnly = false)
    {
        var cue = Cue(entry);
        if (actor.Length > 0 && entry.Actor != actor || category.Length > 0 && cue.Category != category
            || level.Length > 0 && cue.Level != level || status.Length > 0 && Status(entry.Outcome) != status && entry.Outcome != status
            || imagesOnly && entry.Screenshot is null) return false;
        return search.Length == 0 || string.Join('\n', entry.Actor, entry.ActorName, entry.Operation, entry.Summary, entry.Outcome, cue.Level, cue.Category, entry.Source)
            .Contains(search.Trim(), StringComparison.OrdinalIgnoreCase);
    }
}
