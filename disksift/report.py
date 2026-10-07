"""テキストと JSON の出力、端末の桁そろえ。"""

import json
import unicodedata
from dataclasses import asdict

from . import __version__
from .clean import Plan, Result
from .scan import Env, Item, human


def cw(s: str) -> int:
    """端末での桁数。全角(W/F)は2桁。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def fit(s: str, width: int) -> str:
    """width 桁に切って、足りなければ空白で埋める。"""
    out, w = [], 0
    for c in s:
        cwc = cw(c)
        if w + cwc > width:
            break
        out.append(c)
        w += cwc
    return "".join(out) + " " * (width - w)


def rjust(s: str, width: int) -> str:
    return " " * max(0, width - cw(s)) + s


def wrap(s: str, width: int) -> list[str]:
    """桁数で折り返す(日本語は語の切れ目が無いので文字単位)。"""
    lines, cur, w = [], "", 0
    for c in s:
        cwc = cw(c)
        if w + cwc > width and cur:
            lines.append(cur)
            cur, w = "", 0
        cur += c
        w += cwc
    return lines + [cur] if cur or not lines else lines


def status(it: Item) -> str:
    if not it.available:
        return "無し"
    if not it.cleanable:
        return "案内のみ"
    return "掃除可(root)" if it.needs_root else "掃除可"


def count_text(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


TITLE_W = 30


def summary_row(it: Item) -> str:
    size = human(it.size) if it.available else "-"
    reclaim = ""
    if it.available and it.reclaim is not None and it.reclaim != it.size:
        reclaim = f"空く量 {human(it.reclaim)}"
    return (f"{rjust(size, 10)}  {rjust(count_text(it.count if it.available else None), 8)}  "
            f"{fit(it.title, TITLE_W)}  {fit(status(it), 12)}  {reclaim}").rstrip()


def summary_header() -> str:
    return f"{rjust('大きさ', 10)}  {rjust('件数', 8)}  {fit('項目', TITLE_W)}  {fit('掃除', 12)}".rstrip()


def text_report(env: Env, items: list[Item], top: int = 10) -> str:
    out = [f"DiskSift {__version__}   根: {env.root}   ホーム: {env.home}", ""]
    out.append(summary_header())
    out += [summary_row(it) for it in items]
    for it in items:
        out += ["", f"■ {it.title} [{it.key}]"]
        if not it.available:
            out.append(f"  無し: {it.note}")
            continue
        out.append(f"  大きさ {human(it.size)} / 件数 {count_text(it.count)}"
                   + (f" / 空く量 {human(it.reclaim)}" if it.reclaim is not None else ""))
        out.append(f"  何なのか: {it.about}")
        out.append(f"  消すと: {it.effect}")
        out.append(f"  掃除: {it.how}")
        if it.skipped:
            out.append(f"  読めずに飛ばしたディレクトリ: {it.skipped} 件")
        if it.note:
            out.append(f"  メモ: {it.note}")
        for d in it.details[:top]:
            note = f"  {d.note}" if d.note else ""
            out.append(f"    {rjust(human(d.size), 10)}  {d.label}{note}")
        if len(it.details) > top:
            out.append(f"    (ほか {len(it.details) - top} 件。--json で全部)")
    return "\n".join(out) + "\n"


def json_report(env: Env, items: list[Item]) -> str:
    data = {
        "version": __version__,
        "root": str(env.root),
        "home": str(env.home),
        "items": [asdict(it) for it in items],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def plan_lines(plan: Plan) -> list[str]:
    out = []
    if plan.guide:
        return [f"{plan.title}: {plan.blocked}", f"  案内: {plan.guide}"]
    if plan.command:
        out.append("実行するコマンド:")
        out.append("  " + " ".join(plan.command))
        out.append(f"空く量の目安: {human(plan.total)}")
    else:
        out.append("消すもの:")
        out += [f"  {rjust(human(s), 10)}  {p}" for p, s in plan.entries]
        out.append(f"合計 {len(plan.entries)} 件 / {human(plan.total)}")
    if plan.blocked:
        out.append(f"実行できない: {plan.blocked}")
    return out


def result_lines(plan: Plan, res: Result) -> list[str]:
    if plan.command:
        out = [f"実行しました: {' '.join(plan.command)}"]
    else:
        out = [f"消しました: {res.deleted} 件 / {human(res.freed)}"]
    if res.output.strip():
        out += ["  " + line for line in res.output.strip().splitlines()]
    if res.failed:
        out.append(f"失敗: {len(res.failed)} 件")
        out += [f"  {p}: {why}" for p, why in res.failed]
    return out
