"""Short chat notices and readable Markdown reports; no network or desktop IO."""
from __future__ import annotations
import re


def plain(text):
    return " ".join(str(text).split())


def clauses(text):
    # Do not split decimal prices, dates, quantities, or their units.
    parts = [re.sub(r"^[-•]\s+", "", p.strip()) for p in re.split(r"(?<=[。；！？])|[\r\n]+", text)]
    return [p for p in parts if p]


def excerpt(text, limit):
    selected = []
    for part in clauses(text):
        part = plain(part)
        if len("".join(selected)) + len(part) > limit:
            break  # Never cut a price, unit, condition, or sentence in half.
        selected.append(part)
        if len(selected) == 2:
            break
    return "".join(selected) or "本段较长，完整摘要已保存为 Markdown。"


def brief_parts(marker, reports, summaries, config, replay=True):
    title = "【群聊摘要·历史回放】" if replay else "【群聊新增摘要】"
    parts = [title]
    for report in reports:
        source = report["source"]
        text = summaries.get(source["id"], "本批没有可总结的消息。")
        parts.append(f"{plain(source['name'])}：{excerpt(text, config.get('maxGroupBriefCharacters', 95))}")
    parts.append(f"〔{marker}〕")
    # Message previews can have paragraphs; native keyboard delivery remains one line.
    if len(" ".join(parts)) > min(config.get("maxBriefCharacters", 360), config["maxDeliveryCharacters"]):
        raise ValueError("BRIEF_TOO_LONG_REVIEW_REQUIRED")
    return parts


def brief_text(marker, reports, summaries, config, replay=True):
    return " ".join(brief_parts(marker, reports, summaries, config, replay))


def md_text(text):
    # Untrusted group text stays literal: no injected links, HTML or remote images.
    text = plain(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)


def report_markdown(marker, reports, summaries, config, records, created_at, replay=True):
    title = "群聊摘要 · 历史回放" if replay else "群聊摘要 · 新增消息"
    scope = "这是最近记录的回放，不是实时新消息提醒。" if replay else "仅包含本轮监控期间的新增消息。"
    lines = [f"# {title}", "", scope, "", f"生成时间：{md_text(created_at)}", "",
             "## 一眼看完", ""]
    for report in reports:
        source = report["source"]
        text = summaries.get(source["id"], "本批没有可总结的消息。")
        lines.append(f"- **{md_text(source['name'])}**：{md_text(excerpt(text, config.get('maxGroupBriefCharacters', 95)))}")
    lines += ["", "## 统计", "", "| 群聊 | 读取消息 | 关键词命中消息 |", "| --- | ---: | ---: |"]
    for report in reports:
        lines.append(f"| {md_text(report['source']['name'])} | {report['messageCount']} | {report['hitCount']} |")
    lines += ["", "同一条消息命中多个关键词，只计一条命中消息。", ""]
    for report in reports:
        source = report["source"]
        text = summaries.get(source["id"], "本批没有可总结的消息。")
        lines += [f"## {md_text(source['name'])}", "", "### 完整摘要", ""]
        lines += ["- " + md_text(part) for part in clauses(text)]
        lines += ["", "### 关键词", "", "；".join(f"{md_text(k)}：{v} 条" for k, v in report["keywordCounts"].items()), ""]
        lines += ["### 最近命中摘录", ""]
        if not report["hits"]:
            lines += ["本批没有命中。", ""]
        for hit in report["hits"][-3:]:
            lines += [f"**{md_text(hit['time'])} · {md_text(hit['sender'])}**", "",
                      "> " + md_text(hit["text"]), ""]
    lines += ["---", "", "以上为群消息内容整理，报价、单位、时效和成交状态以原消息为准。", "",
              f"报告标识：{md_text(marker)}", ""]
    return "\n".join(lines)
