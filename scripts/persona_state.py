#!/usr/bin/env python3
"""State manager for persona-writing-skill (standard library only)."""

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOME = Path.home() / ".persona-writing-skill"
CURRENT_TEMPLATE = """# 当前人物卡

- 状态：未设置
- 卡片名称：
- 卡片路径：
- 设置日期：
- 设置依据：
"""
FEEDBACK_TEMPLATE = """# 用户反馈记录

只记录能改善人物卡或改写质量的反馈，不记录无关隐私。

| 日期 | 原人物卡 | 问题类型 | 用户反馈摘要 | 处理结果 |
|---|---|---|---|---|
"""
INDEX_HEADER = """# 人物卡索引

| 名称 | 类型 | 路径 | 主领域 | 核心风格 |
|---|---|---|---|---|
"""


class StateError(RuntimeError):
    pass


def get_state_dir(value):
    configured = value or os.environ.get("PERSONA_WRITING_HOME")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_HOME.resolve()


def get_date(value=None):
    if not value:
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise StateError("日期必须使用 YYYY-MM-DD 格式") from exc


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content.rstrip() + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def cell(value):
    return re.sub(r"\s+", " ", str(value).strip()).replace("|", r"\|")


def parse_index(path):
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        parts = [
            part.strip().replace(r"\|", "|")
            for part in re.split(r"(?<!\\)\|", line.strip("|"))
        ]
        if len(parts) != 5 or parts[0] in {"名称", "---"}:
            continue
        rows.append(
            dict(
                name=parts[0],
                type=parts[1],
                path=parts[2].strip("`"),
                domain=parts[3],
                style=parts[4],
            )
        )
    return rows


def seed_rows():
    source = SKILL_ROOT / "records" / "人物卡索引.md"
    return [row for row in parse_index(source) if row["type"] == "种子"]


def infer_name(path):
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (IndexError, OSError, UnicodeError):
        first = ""
    if first.startswith("#"):
        title = re.split(r"\s+[—–-]\s+", first.lstrip("#").strip(), maxsplit=1)[0]
        if title:
            return title
    return re.sub(r"-\d{2}$", "", re.sub(r"^\d{8}-", "", path.stem))


def custom_rows(home):
    previous = {
        row["path"]: row
        for row in parse_index(home / "records" / "人物卡索引.md")
        if row["type"] == "专属"
    }
    rows = []
    for path in sorted((home / "cards" / "custom").glob("*.md")):
        relative = "cards/custom/" + path.name
        old = previous.get(relative, {})
        rows.append(
            dict(
                name=old.get("name", infer_name(path)),
                type="专属",
                path=relative,
                domain=old.get("domain", "待补充"),
                style=old.get("style", "待补充"),
            )
        )
    return rows


def all_rows(home):
    return seed_rows() + custom_rows(home)


def card_path(home, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise StateError("人物卡路径不安全：" + relative)
    if path.parts[:2] == ("cards", "seeds"):
        base = (SKILL_ROOT / "cards" / "seeds").resolve()
        target = (SKILL_ROOT / path).resolve()
    elif path.parts[:2] == ("cards", "custom"):
        base = (home / "cards" / "custom").resolve()
        target = (home / path).resolve()
    else:
        raise StateError("人物卡路径不受支持：" + relative)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise StateError("人物卡路径逃逸允许目录：" + relative) from exc
    return target


def located_rows(home):
    return [
        dict(row, absolute_path=str(card_path(home, row["path"])))
        for row in all_rows(home)
    ]


def render_index(rows):
    lines = [INDEX_HEADER.rstrip()]
    for row in rows:
        lines.append(
            "| {name} | {type} | `{path}` | {domain} | {style} |".format(
                **{key: cell(value) for key, value in row.items()}
            )
        )
    lines += [
        "",
        "索引由 `scripts/persona_state.py` 管理；"
        "种子卡位于 Skill 目录，专属卡位于外置状态目录。",
    ]
    return "\n".join(lines)


def repair(home):
    rows = all_rows(home)
    write_atomic(home / "records" / "人物卡索引.md", render_index(rows))
    return rows


def initialize(home):
    (home / "cards" / "custom").mkdir(parents=True, exist_ok=True)
    records = home / "records"
    records.mkdir(parents=True, exist_ok=True)
    if not (records / "当前人物卡.md").exists():
        write_atomic(records / "当前人物卡.md", CURRENT_TEMPLATE)
    if not (records / "用户反馈记录.md").exists():
        write_atomic(records / "用户反馈记录.md", FEEDBACK_TEMPLATE)
    index = records / "人物卡索引.md"
    if not index.exists():
        repair(home)


def find_card(home, identifier):
    needle = identifier.strip()
    exact, partial = [], []
    for row in all_rows(home):
        values = {row["name"], row["path"], Path(row["path"]).name, Path(row["path"]).stem}
        if needle in values:
            exact.append(row)
        elif needle.casefold() in row["name"].casefold():
            partial.append(row)
    matches = exact or partial
    if not matches:
        raise StateError("找不到人物卡：" + identifier)
    if len(matches) > 1:
        choices = "、".join(row["name"] + " (" + row["path"] + ")" for row in matches)
        raise StateError("人物卡标识不唯一：" + choices)
    if not card_path(home, matches[0]["path"]).is_file():
        raise StateError("人物卡文件不存在：" + matches[0]["path"])
    return matches[0]


def read_current(home):
    result = dict(status="未设置", name="", path="", date="", basis="")
    path = home / "records" / "当前人物卡.md"
    if not path.is_file():
        return result
    keys = {
        "状态": "status",
        "卡片名称": "name",
        "卡片路径": "path",
        "设置日期": "date",
        "设置依据": "basis",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^-\s*([^：]+)：(.*)$", line)
        if match and match.group(1).strip() in keys:
            result[keys[match.group(1).strip()]] = match.group(2).strip()
    if result["path"]:
        result["absolute_path"] = str(card_path(home, result["path"]))
    return result


def set_current(home, row, basis, date_value=None):
    date = get_date(date_value)
    content = f"""# 当前人物卡

- 状态：已设置
- 卡片名称：{cell(row["name"])}
- 卡片路径：{row["path"]}
- 设置日期：{date}
- 设置依据：{cell(basis)}
"""
    write_atomic(home / "records" / "当前人物卡.md", content)
    return dict(
        status="已设置",
        name=row["name"],
        path=row["path"],
        absolute_path=str(card_path(home, row["path"])),
        date=date,
        basis=basis,
    )


def safe_name(name):
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "-", name.strip())
    value = re.sub(r"-{2,}", "-", re.sub(r"\s+", "-", value)).strip("-. ")
    if not value:
        raise StateError("人物卡名称无法生成安全文件名")
    return value[:80]


def save_card(home, args):
    initialize(home)
    source = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8")
    if not source.strip():
        raise StateError("人物卡内容不能为空")
    date = get_date(args.date)
    base = date.replace("-", "") + "-" + safe_name(args.name)
    target = home / "cards" / "custom" / (base + ".md")
    suffix = 2
    while target.exists():
        target = target.with_name(f"{base}-{suffix:02d}.md")
        suffix += 1
    content = source.strip()
    if not content.startswith("#"):
        content = "# " + args.name + "\n\n" + content
    write_atomic(target, content)

    relative = "cards/custom/" + target.name
    rows = custom_rows(home)
    for row in rows:
        if row["path"] == relative:
            row.update(
                name=args.name.strip(),
                domain=args.domain.strip() or "待补充",
                style=args.style.strip() or "待补充",
            )
    write_atomic(home / "records" / "人物卡索引.md", render_index(seed_rows() + rows))
    row = dict(
        name=args.name.strip(),
        type="专属",
        path=relative,
        domain=args.domain.strip() or "待补充",
        style=args.style.strip() or "待补充",
    )
    result = dict(saved=row, absolute_path=str(target))
    if args.set_current:
        result["current"] = set_current(home, row, args.basis, date)
    return result


def add_feedback(home, args):
    initialize(home)
    aliases = {
        "execution": "执行问题",
        "card": "人物卡问题",
        "执行问题": "执行问题",
        "人物卡问题": "人物卡问题",
    }
    problem_type = aliases.get(args.problem_type)
    if not problem_type:
        raise StateError("问题类型必须是 execution、card、执行问题或人物卡问题")
    if not args.feedback.strip() or not args.result.strip():
        raise StateError("反馈摘要和处理结果不能为空")
    row = dict(
        date=get_date(args.date),
        card=args.card.strip() or "未指定",
        type=problem_type,
        feedback=args.feedback.strip(),
        result=args.result.strip(),
    )
    path = home / "records" / "用户反馈记录.md"
    line = "| {date} | {card} | {type} | {feedback} | {result} |".format(
        **{key: cell(value) for key, value in row.items()}
    )
    write_atomic(path, path.read_text(encoding="utf-8").rstrip() + "\n" + line)
    return row


def validate(home):
    errors, warnings = [], []
    required = [
        "records/当前人物卡.md",
        "records/人物卡索引.md",
        "records/用户反馈记录.md",
    ]
    for relative in required:
        if not (home / relative).is_file():
            errors.append("缺少状态文件：" + relative)
    rows = parse_index(home / "records" / "人物卡索引.md")
    names, paths = set(), set()
    for row in rows:
        if row["name"] in names:
            warnings.append("人物卡名称重复：" + row["name"])
        if row["path"] in paths:
            errors.append("索引路径重复：" + row["path"])
        names.add(row["name"])
        paths.add(row["path"])
        try:
            if not card_path(home, row["path"]).is_file():
                errors.append("索引指向不存在文件：" + row["path"])
        except StateError as exc:
            errors.append(str(exc))

    disk = {"cards/custom/" + path.name for path in (home / "cards" / "custom").glob("*.md")}
    indexed = {row["path"] for row in rows if row["type"] == "专属"}
    errors += ["专属卡未进入索引：" + path for path in sorted(disk - indexed)]
    errors += ["索引包含失效专属卡：" + path for path in sorted(indexed - disk)]

    try:
        current = read_current(home)
    except StateError as exc:
        errors.append(str(exc))
        current = dict(status="未设置", path="")
    if current["status"] == "已设置":
        if not current["path"]:
            errors.append("当前人物卡已设置但路径为空")
        elif not card_path(home, current["path"]).is_file():
            errors.append("当前人物卡文件不存在：" + current["path"])
    return dict(
        valid=not errors,
        state_dir=str(home),
        card_count=len(rows),
        errors=errors,
        warnings=warnings,
    )


def output(payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, list):
        print("| 名称 | 类型 | 路径 | 主领域 | 核心风格 |")
        print("|---|---|---|---|---|")
        for row in payload:
            print("| {name} | {type} | `{path}` | {domain} | {style} |".format(**row))
    else:
        for key, value in payload.items():
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            print(f"{key}: {rendered}")


def parser():
    root = argparse.ArgumentParser(description="管理人物文风工厂的持久化状态")
    root.add_argument(
        "--state-dir",
        help="默认读取 PERSONA_WRITING_HOME，否则使用 ~/.persona-writing-skill",
    )
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("init", "初始化外置状态目录"),
        ("list", "列出全部人物卡"),
        ("current", "读取当前人物卡"),
        ("repair-index", "根据磁盘重建索引"),
        ("validate", "检查状态一致性"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--state-dir", default=argparse.SUPPRESS)
        command.add_argument("--json", action="store_true")

    command = commands.add_parser("set-current", help="设置当前人物卡")
    command.add_argument("--state-dir", default=argparse.SUPPRESS)
    command.add_argument("identifier")
    command.add_argument("--basis", default="用户明确指定")
    command.add_argument("--date")
    command.add_argument("--json", action="store_true")

    command = commands.add_parser("save", help="安全保存专属人物卡")
    command.add_argument("--state-dir", default=argparse.SUPPRESS)
    command.add_argument("--name", required=True)
    command.add_argument("--source", required=True, help="Markdown 文件路径；- 表示 stdin")
    command.add_argument("--domain", default="待补充")
    command.add_argument("--style", default="待补充")
    command.add_argument("--set-current", action="store_true")
    command.add_argument("--basis", default="用户确认的专属人物卡")
    command.add_argument("--date")
    command.add_argument("--json", action="store_true")

    command = commands.add_parser("add-feedback", help="追加风格反馈")
    command.add_argument("--state-dir", default=argparse.SUPPRESS)
    command.add_argument("--card", default="未指定")
    command.add_argument("--type", required=True, dest="problem_type")
    command.add_argument("--feedback", required=True)
    command.add_argument("--result", required=True)
    command.add_argument("--date")
    command.add_argument("--json", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    home = get_state_dir(args.state_dir)
    as_json = getattr(args, "json", False)
    try:
        if args.command == "init":
            initialize(home)
            result = dict(initialized=True, state_dir=str(home), card_count=len(all_rows(home)))
        elif args.command == "list":
            initialize(home)
            result = located_rows(home)
        elif args.command == "current":
            initialize(home)
            result = read_current(home)
        elif args.command == "set-current":
            initialize(home)
            result = set_current(home, find_card(home, args.identifier), args.basis, args.date)
        elif args.command == "save":
            result = save_card(home, args)
        elif args.command == "add-feedback":
            result = add_feedback(home, args)
        elif args.command == "repair-index":
            initialize(home)
            result = dict(repaired=True, cards=repair(home))
        elif args.command == "validate":
            result = validate(home)
            output(result, as_json)
            return 0 if result["valid"] else 1
        output(result, as_json)
        return 0
    except (OSError, UnicodeError, StateError) as exc:
        message = json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False) if as_json else "错误：" + str(exc)
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
