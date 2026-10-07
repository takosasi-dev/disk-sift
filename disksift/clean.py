"""掃除の予定づくりと実行。

安全策:
- 消せるのは pacman-cache / journal / coredump / user-cache / trash / old-logs だけ。
- 消す直前に、実パス(親ディレクトリを realpath で解決)が許可した根の下にあるか確かめる。
- シンボリックリンクは辿らない。リンクそのものを消し、リンク先には触らない。
- root が要る項目を一般ユーザーで実行しようとしたら、消さずに止める。
"""

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import scan
from .scan import Env

CLEANABLE = ("pacman-cache", "journal", "coredump", "user-cache", "trash", "old-logs")


@dataclass
class Plan:
    key: str
    title: str
    entries: list[tuple[Path, int]] = field(default_factory=list)  # 消すもの
    command: list[str] | None = None  # 代わりに実行するコマンド
    total: int | None = 0
    roots: list[Path] = field(default_factory=list)  # この下だけ消してよい
    blocked: str = ""  # 空でなければ実行しない(理由)
    guide: str = ""  # 案内だけの項目の案内


@dataclass
class Result:
    deleted: int = 0
    freed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    output: str = ""


class PlanError(ValueError):
    pass


def make_plan(env: Env, key: str, only: tuple = (), vacuum_size: str = scan.DEFAULT_VACUUM) -> Plan:
    if key not in scan.INFO:
        raise PlanError(f"知らない項目: {key}(使えるもの: {', '.join(scan.ORDER)})")
    title, _, _, how, _, needs_root = scan.INFO[key]
    plan = Plan(key, title)
    if key not in CLEANABLE:
        plan.blocked = "この項目は案内だけで、DiskSift は消さない"
        plan.guide = how
        plan.total = None
        return plan

    if key == "pacman-cache":
        d = env.path("var/cache/pacman/pkg")
        plan.roots = [d]
        if d.is_dir():
            plan.entries, _ = scan.pacman_old_files(env)
    elif key == "journal":
        size = scan.parse_vacuum_size(vacuum_size)
        if size is None:
            raise PlanError(f"--vacuum-size の形がおかしい: {vacuum_size}(例: 200M, 1G)")
        plan.command = ["journalctl", f"--vacuum-size={vacuum_size}"]
        it = scan.scan_journal(env)
        plan.total = max(0, it.size - size) if it.available and it.size is not None else None
        if env.run(["journalctl", "--version"]) is None:
            plan.blocked = "journalctl が使えない"
    elif key == "coredump":
        d = env.path("var/lib/systemd/coredump")
        plan.roots = [d]
        plan.entries = _sized(env, scan.children(d))
    elif key == "user-cache":
        plan.roots = scan.user_cache_roots(env)
        entries = scan.user_cache_entries(env)
        if only:
            want = set(only)
            entries = [e for e in entries if e[0] in want or Path(e[0]).name in want]
            if not entries:
                raise PlanError(f"--only に合うキャッシュが無い: {', '.join(only)}")
        plan.entries = _sized(env, [p for _, p, _ in entries])
    elif key == "trash":
        dirs = scan.trash_dirs(env)
        plan.roots = dirs
        plan.entries = _sized(env, [c for d in dirs for c in scan.children(d)])
    elif key == "old-logs":
        plan.roots = [env.path("var/log")]
        plan.entries, _ = scan.old_log_files(env)

    if plan.command is None:
        plan.total = sum(s for _, s in plan.entries)
    if needs_root and env.euid != 0 and not plan.blocked:
        plan.blocked = f"root が要る(sudo disksift clean {key} で実行してください)"
    return plan


def _sized(env: Env, paths: list[Path]) -> list[tuple[Path, int]]:
    seen: set = set()
    return [(p, scan.du(p, env, seen).size) for p in paths]


def inside(path: Path, roots: list[Path]) -> bool:
    """path(リンクなら辿らない)の実パスが、どれかの根の真下より深いところにあるか。"""
    if path.name in ("", ".", ".."):
        return False
    real = os.path.join(os.path.realpath(path.parent), path.name)
    for r in roots:
        rr = os.path.realpath(r)
        try:
            if real != rr and os.path.commonpath([rr, real]) == rr:
                return True
        except ValueError:  # ドライブ違いなど
            continue
    return False


def remove(path: Path) -> None:
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)  # rmtree は中のシンボリックリンクを辿らない
    else:
        os.unlink(path)  # リンクならリンクそのものだけ消える


def execute(env: Env, plan: Plan) -> Result:
    res = Result()
    if plan.blocked:
        raise PlanError(plan.blocked)
    if plan.command:
        r = env.run(plan.command)
        if r is None:
            res.failed.append((" ".join(plan.command), "実行できなかった"))
        else:
            res.output = r[1]
            if r[0] != 0:
                res.failed.append((" ".join(plan.command), f"終了コード {r[0]}"))
            else:
                res.freed = plan.total or 0
        return res
    for p, size in plan.entries:
        if not inside(p, plan.roots):
            res.failed.append((str(p), "決めた場所の外なので消さない"))
            continue
        try:
            remove(p)
        except FileNotFoundError:
            continue
        except OSError as e:
            res.failed.append((str(p), e.strerror or str(e)))
            continue
        res.deleted += 1
        res.freed += size
    return res
