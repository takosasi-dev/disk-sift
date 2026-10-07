"""原因別にディスクの使い方を数える(画面は持たない)。

外から読むもの(ファイルの根、ホーム、コマンドの実行)は Env に集めてあり、
テストでは一時ディレクトリと偽のコマンドに差し替える。
"""

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from functools import cmp_to_key
from pathlib import Path

from . import pkgver


def run_cmd(args: list[str]):
    """コマンドを実行して (終了コード, 標準出力)。コマンドが無い・動かないときは None。"""
    if shutil.which(args[0]) is None:
        return None
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=120,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.returncode, p.stdout


def no_cmd(args: list[str]):
    """--root で別の根を見ているとき用。手元のシステムのコマンドの結果を混ぜない。"""
    return None


@dataclass
class Env:
    root: Path
    home: Path
    euid: int
    run: object = run_cmd
    # 数え中の進み具合(TUI が別スレッドから読む)
    counted: int = 0
    current: str = ""

    def path(self, rel: str) -> Path:
        return self.root / rel


def default_env(root: str | None = None, home: str | None = None) -> Env:
    euid = os.geteuid() if hasattr(os, "geteuid") else -1
    if home is None:
        home = os.path.expanduser("~")
        # sudo で動かしたときは、呼んだ人のホームを見る
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and euid == 0:
            try:
                import pwd
                home = pwd.getpwnam(sudo_user).pw_dir
            except (ImportError, KeyError):
                pass
    r = Path(root or "/")
    return Env(root=r, home=Path(home), euid=euid,
               run=run_cmd if str(r) in ("/", "\\") else no_cmd)


# ---- 大きさの計算 ---------------------------------------------------------

@dataclass
class Usage:
    size: int = 0
    files: int = 0
    skipped: int = 0  # 読めなかったディレクトリ


def du(path, env: Env | None = None, seen: set | None = None,
       exclude: frozenset = frozenset()) -> Usage:
    """path の下の通常ファイルの大きさ(st_size)の合計。

    シンボリックリンクは辿らない。ハードリンクは (dev, inode) で1回だけ数える。
    読めないディレクトリは飛ばして skipped に数える。
    """
    u = Usage()
    seen = set() if seen is None else seen
    stack = [str(path)]
    first = True
    while stack:
        p = stack.pop()
        try:
            st = os.lstat(p)
        except OSError:
            if first:
                return u
            continue
        first = False
        if stat.S_ISDIR(st.st_mode):
            try:
                names = os.listdir(p)
            except OSError:
                u.skipped += 1
                continue
            for n in names:
                c = os.path.join(p, n)
                if c not in exclude:
                    stack.append(c)
        elif stat.S_ISREG(st.st_mode):
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            u.size += st.st_size
            u.files += 1
            if env is not None:
                env.counted += 1
    return u


# ---- 結果の形 -------------------------------------------------------------

@dataclass
class Detail:
    label: str
    size: int | None
    note: str = ""
    count: int | None = None


@dataclass
class Item:
    key: str
    title: str
    about: str
    effect: str
    how: str
    cleanable: bool
    needs_root: bool
    available: bool = True
    size: int | None = None
    count: int | None = None
    reclaim: int | None = None  # 掃除で空く量(分かるときだけ)
    skipped: int = 0
    note: str = ""
    details: list[Detail] = field(default_factory=list)


INFO = {
    "pacman-cache": (
        "pacman のパッケージキャッシュ",
        "pacman が入れたパッケージの元ファイル(/var/cache/pacman/pkg)。古い版も残り続ける。",
        "古い版を消すと、その版へのダウングレードが手元からはできなくなる。新しい2版は残す。",
        "各パッケージの新しい2版だけ残して消す(paccache -rk2 と同じ考え。.sig も対で消す)。root が要る。",
        True, True),
    "orphans": (
        "孤立パッケージ",
        "依存として入ったが、今はどのパッケージからも必要とされていないパッケージ。",
        "消しても普通は困らないが、自分で使っているものが混ざることがある。中身を確かめてから。",
        "DiskSift は消さない。確かめてから: sudo pacman -Rns $(pacman -Qtdq)",
        False, True),
    "journal": (
        "systemd journal",
        "systemd が集めたログ(/var/log/journal)。",
        "古いログが読めなくなる。障害の調べ物に使うなら残す量を多めに。",
        "journalctl --vacuum-size=<N> で N まで減らす(既定 200M)。root が要る。",
        True, True),
    "coredump": (
        "コアダンプ",
        "落ちたプログラムのメモリの写し(/var/lib/systemd/coredump)。",
        "落ちた原因を coredumpctl で調べられなくなる。",
        "中のファイルを全部消す。root が要る。",
        True, True),
    "user-cache": (
        "ユーザーのキャッシュ",
        "~/.cache や各言語のパッケージ置き場のキャッシュ。消しても必要になれば作り直される。",
        "次に使うときに取り直し・作り直しで遅くなる。動いているアプリのキャッシュは閉じてから。",
        "選んだもの(または全部)を消す。--only で名前を指定できる。",
        True, False),
    "trash": (
        "ゴミ箱",
        "デスクトップのゴミ箱(~/.local/share/Trash)。",
        "ゴミ箱から元に戻せなくなる。",
        "ゴミ箱の中身を全部消す。",
        True, False),
    "old-logs": (
        "ローテート済みのログ",
        "/var/log の古いログ(*.gz *.old *.数字)。",
        "古いログが読めなくなる。今使っているログは消さない。",
        "当てはまるファイルを消す。root が要る。",
        True, True),
    "containers": (
        "Docker / Podman",
        "コンテナのイメージ・コンテナ・ボリューム・ビルドキャッシュ(system df の結果)。",
        "使っていないイメージやボリュームを消すと、取り直し・作り直しになる。ボリュームのデータは戻らない。",
        "DiskSift は消さない。確かめてから: docker system prune / podman system prune",
        False, False),
    "other": (
        "その他(ホームの大きいもの)",
        "上のどれにも入らない、ホームの中の大きいディレクトリ。",
        "自分のデータなので、何なのか分かってから。",
        "DiskSift は消さない。中身を確かめて自分で整理する。",
        False, False),
}

ORDER = list(INFO)


def new_item(key: str) -> Item:
    title, about, effect, how, cleanable, needs_root = INFO[key]
    return Item(key, title, about, effect, how, cleanable, needs_root)


def unavailable(key: str, note: str) -> Item:
    it = new_item(key)
    it.available = False
    it.note = note
    return it


# ---- 単位 -----------------------------------------------------------------

def human(n: int | None) -> str:
    if n is None:
        return "取れない"
    if n < 1024:
        return f"{n} B"
    v = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        v /= 1024
        if v < 1024:
            break
    return f"{v:.1f} {unit}"


_UNIT_POW = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}


def parse_human(num: str, unit: str, base: int = 1024) -> int:
    return int(float(num) * base ** _UNIT_POW[unit.upper()])


def parse_vacuum_size(text: str) -> int | None:
    """journalctl の --vacuum-size に渡す形(200M など)。おかしければ None。"""
    m = re.fullmatch(r"(\d+)([KMGT]?)", text.strip(), re.IGNORECASE)
    return parse_human(m[1], m[2]) if m else None


# ---- 1. pacman キャッシュ -------------------------------------------------

def _make_vercmp(env: Env):
    r = env.run(["vercmp", "1", "1"])
    if r is None or r[0] != 0:
        return pkgver.vercmp

    def cmd(a, b):
        out = env.run(["vercmp", a, b])
        try:
            v = int(out[1].strip())
        except (TypeError, ValueError, IndexError):
            return pkgver.vercmp(a, b)
        return (v > 0) - (v < 0)
    return cmd


def pacman_old_files(env: Env, keep: int = 2):
    """新しい keep 版より古いパッケージファイルと .sig。[(path, size)], パッケージ数"""
    d = env.path("var/cache/pacman/pkg")
    groups: dict[tuple, list] = {}
    for e in os.scandir(d):
        if e.name.endswith(".sig") or not e.is_file(follow_symlinks=False):
            continue
        parsed = pkgver.parse_pkg_filename(e.name)
        if parsed:
            name, ver, arch = parsed
            groups.setdefault((name, arch), []).append((ver, Path(e.path)))
    npkg = sum(len(v) for v in groups.values())
    out = []
    cmp = None
    for files in groups.values():
        if len(files) <= keep:
            continue
        cmp = cmp or _make_vercmp(env)
        files.sort(key=cmp_to_key(lambda x, y: cmp(x[0], y[0])), reverse=True)
        for _, p in files[keep:]:
            for q in (p, p.with_name(p.name + ".sig")):
                try:
                    st = os.lstat(q)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    out.append((q, st.st_size))
    return out, npkg


def scan_pacman_cache(env: Env) -> Item:
    d = env.path("var/cache/pacman/pkg")
    if not d.is_dir():
        return unavailable("pacman-cache", "パッケージキャッシュの置き場が無い(pacman を使っていない?)")
    it = new_item("pacman-cache")
    u = du(d, env)
    it.size, it.skipped = u.size, u.skipped
    try:
        old, it.count = pacman_old_files(env)
    except OSError as e:
        it.note = f"中を読めない: {e.strerror}"
        return it
    it.reclaim = sum(s for _, s in old)
    by_pkg: dict[str, list] = {}
    for p, s in old:
        if not p.name.endswith(".sig"):
            name = pkgver.parse_pkg_filename(p.name)[0]
            by_pkg.setdefault(name, []).append(s)
    it.details = sorted(
        (Detail(n, sum(v), f"古い版 {len(v)} 個", len(v)) for n, v in by_pkg.items()),
        key=lambda x: -x.size)
    if not old:
        it.note = "どのパッケージも2版以下なので、消すものは無い"
    return it


# ---- 2. 孤立パッケージ ----------------------------------------------------

_INSTALLED_SIZE = re.compile(r"^Installed Size\s*:\s*([\d.]+)\s*([KMGTP]?)i?B", re.M)
_QI_NAME = re.compile(r"^Name\s*:\s*(\S+)", re.M)


def scan_orphans(env: Env) -> Item:
    r = env.run(["pacman", "-Qtdq"])
    if r is None:
        return unavailable("orphans", "pacman コマンドが使えない")
    rc, out = r
    it = new_item("orphans")
    names = out.split()
    if rc not in (0, 1):
        it.note = f"pacman -Qtdq が失敗した(終了コード {rc})"
        return it
    it.count = len(names)
    if not names:
        it.size = 0
        return it
    q = env.run(["pacman", "-Qi", *names])
    if q is None or q[0] != 0:
        it.note = "pacman -Qi で大きさを取れなかった"
        it.details = [Detail(n, None) for n in names]
        return it
    sizes = {}
    for block in q[1].split("\n\n"):
        n, s = _QI_NAME.search(block), _INSTALLED_SIZE.search(block)
        if n and s:
            sizes[n[1]] = parse_human(s[1], s[2])
    it.details = sorted((Detail(n, sizes.get(n)) for n in names),
                        key=lambda d: -(d.size or 0))
    if len(sizes) == len(names):
        it.size = sum(sizes.values())
    else:
        it.note = "一部のパッケージの大きさを取れなかった"
    return it


# ---- 3. journal -----------------------------------------------------------

_JOURNAL_USAGE = re.compile(r"take up ([\d.]+)\s*([KMGTPE]?)B?\b")
DEFAULT_VACUUM = "200M"


def scan_journal(env: Env) -> Item:
    jdir = env.path("var/log/journal")
    r = env.run(["journalctl", "--disk-usage"])
    m = _JOURNAL_USAGE.search(r[1]) if r and r[0] == 0 else None
    has_dir = jdir.is_dir()
    if not m and not has_dir:
        return unavailable("journal", "journalctl が使えず、/var/log/journal も無い")
    it = new_item("journal")
    u = du(jdir, env) if has_dir else None
    if u:
        it.count, it.skipped = u.files, u.skipped
    if m:
        it.size = parse_human(m[1], m[2])
    else:
        it.size = u.size
        it.note = "journalctl が使えないので、ファイルの大きさを直接数えた(掃除には journalctl が要る)"
    it.reclaim = max(0, it.size - parse_vacuum_size(DEFAULT_VACUUM))
    it.details = [Detail(f"既定({DEFAULT_VACUUM})まで減らしたときに空く量の目安", it.reclaim)]
    return it


# ---- 4. コアダンプ / 6. ゴミ箱 などの「中身を全部」系 ----------------------

def children(d: Path) -> list[Path]:
    try:
        return sorted(Path(e.path) for e in os.scandir(d))
    except OSError:
        return []


def _sized_children(env: Env, dirs: list[Path]):
    """dirs の直下のものを (path, Usage) で。ハードリンクは全体で1回。"""
    seen: set = set()
    return [(c, du(c, env, seen)) for d in dirs for c in children(d)]


def scan_coredump(env: Env) -> Item:
    d = env.path("var/lib/systemd/coredump")
    if not d.is_dir():
        return unavailable("coredump", "コアダンプの置き場が無い")
    it = new_item("coredump")
    entries = _sized_children(env, [d])
    it.size = sum(u.size for _, u in entries)
    it.count = sum(u.files for _, u in entries)
    it.skipped = sum(u.skipped for _, u in entries)
    if not os.access(d, os.R_OK | os.X_OK):
        it.skipped += 1
        it.size = None
        it.note = "読めない(root で実行すると数えられる)"
    it.reclaim = it.size
    it.details = sorted((Detail(p.name, u.size) for p, u in entries), key=lambda x: -x.size)
    return it


def trash_dirs(env: Env) -> list[Path]:
    t = env.home / ".local/share/Trash"
    return [t / "files", t / "info", t / "expunged"]


def scan_trash(env: Env) -> Item:
    t = env.home / ".local/share/Trash"
    if not t.is_dir():
        return unavailable("trash", "ゴミ箱が無い")
    it = new_item("trash")
    u = du(t, env)
    it.size = it.reclaim = u.size
    it.skipped = u.skipped
    files = _sized_children(env, [t / "files"])
    it.count = len(files)
    it.details = sorted((Detail(p.name, u.size) for p, u in files), key=lambda x: -x.size)
    return it


# ---- 5. ユーザーのキャッシュ ---------------------------------------------

KNOWN_CACHE = {
    "pip": "pip がダウンロードしたパッケージ",
    "uv": "uv(Python)のパッケージ置き場",
    "pypoetry": "Poetry のパッケージ置き場",
    "yay": "AUR ヘルパー yay のビルド置き場",
    "paru": "AUR ヘルパー paru のビルド置き場",
    "go-build": "Go のビルドキャッシュ",
    "mozilla": "Firefox のキャッシュ",
    "chromium": "Chromium のキャッシュ",
    "google-chrome": "Google Chrome のキャッシュ",
    "BraveSoftware": "Brave のキャッシュ",
    "thumbnails": "画像のサムネイル",
    "fontconfig": "フォントの一覧のキャッシュ",
    "mesa_shader_cache": "GPU のシェーダのキャッシュ",
    "yarn": "Yarn のパッケージ置き場",
    "pnpm": "pnpm のキャッシュ",
    "JetBrains": "JetBrains の IDE のキャッシュ",
    "huggingface": "Hugging Face のモデル置き場(取り直しが重い)",
    "electron": "Electron 本体のダウンロード",
    "typescript": "TypeScript の型定義のキャッシュ",
    "deno": "Deno のキャッシュ",
    "bazel": "Bazel のビルドキャッシュ",
}

EXTRA_CACHES = {
    ".npm/_cacache": "npm がダウンロードしたパッケージ",
    ".cargo/registry": "Rust(cargo)のクレート置き場",
    ".gradle/caches": "Gradle の依存とビルドのキャッシュ",
}


def user_cache_entries(env: Env) -> list[tuple[str, Path, str]]:
    """(表示名, パス, 説明)。表示名はホームからの相対パス。"""
    out = []
    for c in children(env.home / ".cache"):
        out.append((f".cache/{c.name}", c, KNOWN_CACHE.get(c.name, "")))
    for rel, desc in EXTRA_CACHES.items():
        p = env.home / rel
        if os.path.lexists(p):
            out.append((rel, p, desc))
    return out


def user_cache_roots(env: Env) -> list[Path]:
    """消してよい場所の根(この直下・中だけ消す)。"""
    return [env.home / ".cache"] + [(env.home / rel).parent for rel in EXTRA_CACHES]


def scan_user_cache(env: Env) -> Item:
    entries = user_cache_entries(env)
    if not entries:
        return unavailable("user-cache", "~/.cache も、よく知られたキャッシュも無い")
    it = new_item("user-cache")
    seen: set = set()
    for label, p, desc in entries:
        u = du(p, env, seen)
        it.skipped += u.skipped
        it.details.append(Detail(label, u.size, desc, u.files))
    it.details.sort(key=lambda x: -x.size)
    it.size = it.reclaim = sum(d.size for d in it.details)
    it.count = sum(d.count for d in it.details)
    return it


# ---- 7. ローテート済みのログ ---------------------------------------------

_ROTATED = re.compile(r"\.(gz|old|\d+)$")


def old_log_files(env: Env):
    """[(path, size)], 読めなかったディレクトリの数。journal の下は見ない。"""
    base = env.path("var/log")
    out, skipped = [], 0
    stack = [str(base)]
    journal = str(base / "journal")
    while stack:
        d = stack.pop()
        try:
            names = os.listdir(d)
        except OSError:
            skipped += 1
            continue
        for n in names:
            p = os.path.join(d, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                if p != journal:
                    stack.append(p)
            elif stat.S_ISREG(st.st_mode) and _ROTATED.search(n):
                out.append((Path(p), st.st_size))
                env.counted += 1
    return sorted(out), skipped


def scan_old_logs(env: Env) -> Item:
    if not env.path("var/log").is_dir():
        return unavailable("old-logs", "/var/log が無い")
    it = new_item("old-logs")
    files, it.skipped = old_log_files(env)
    it.size = it.reclaim = sum(s for _, s in files)
    it.count = len(files)
    base = env.path("var/log")
    it.details = sorted((Detail(str(p.relative_to(base)), s) for p, s in files),
                        key=lambda x: -x.size)
    if it.skipped:
        it.note = "読めないディレクトリがある(root で実行すると全部見える)"
    return it


# ---- 8. Docker / Podman ---------------------------------------------------

_DF_LINE = re.compile(
    r"^(Images|Containers|Local Volumes|Build Cache)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)", re.M)
_DF_SIZE = re.compile(r"([\d.]+)\s*([kKMGTP]?)(i?)B")


def parse_df_size(text: str) -> int | None:
    m = _DF_SIZE.match(text)
    if not m:
        return None
    return parse_human(m[1], m[2], 1024 if m[3] else 1000)


def scan_containers(env: Env) -> Item:
    it = new_item("containers")
    found, notes = False, []
    total, reclaim, count = 0, 0, 0
    for eng in ("docker", "podman"):
        r = env.run([eng, "system", "df"])
        if r is None:
            continue
        found = True
        if r[0] != 0:
            notes.append(f"{eng} system df が失敗した(デーモンが動いていない・権限が無い?)")
            continue
        for m in _DF_LINE.finditer(r[1]):
            size, rec = parse_df_size(m[4]), parse_df_size(m[5])
            total += size or 0
            reclaim += rec or 0
            count += int(m[2])
            it.details.append(Detail(f"{eng} {m[1]}", size,
                                     f"{m[2]} 個、使っていない分 {m[5]}", int(m[2])))
    if not found:
        return unavailable("containers", "docker も podman も無い")
    it.note = " / ".join(notes)
    if it.details:
        it.size, it.reclaim, it.count = total, reclaim, count
    it.details.sort(key=lambda x: -(x.size or 0))
    return it


# ---- 9. その他 ------------------------------------------------------------

OTHER_TOP = 15


def scan_other(env: Env) -> Item:
    if not env.home.is_dir():
        return unavailable("other", "ホームが無い")
    it = new_item("other")
    covered = {str(env.home / ".cache"), str(env.home / ".local/share/Trash")}
    covered |= {str(env.home / rel) for rel in EXTRA_CACHES}
    exclude = frozenset(covered)
    seen: set = set()
    rows = []
    for c in children(env.home):
        if str(c) in exclude:
            continue
        u = du(c, env, seen, exclude)
        it.skipped += u.skipped
        rows.append(Detail(c.name, u.size, "", u.files))
    rows.sort(key=lambda x: -x.size)
    it.size = sum(r.size for r in rows)
    it.count = sum(r.count for r in rows)
    it.details = rows[:OTHER_TOP]
    if len(rows) > OTHER_TOP:
        rest = rows[OTHER_TOP:]
        it.details.append(Detail(f"(ほか {len(rest)} 個)", sum(r.size for r in rest)))
    return it


# ---- まとめ ---------------------------------------------------------------

SCANNERS = {
    "pacman-cache": scan_pacman_cache,
    "orphans": scan_orphans,
    "journal": scan_journal,
    "coredump": scan_coredump,
    "user-cache": scan_user_cache,
    "trash": scan_trash,
    "old-logs": scan_old_logs,
    "containers": scan_containers,
    "other": scan_other,
}


def scan_one(env: Env, key: str) -> Item:
    env.current = INFO[key][0]
    try:
        return SCANNERS[key](env)
    except Exception as e:  # 1項目の失敗で全体を止めない
        it = new_item(key)
        it.note = f"数えられなかった: {e}"
        return it


def scan_all(env: Env, on_item=None) -> list[Item]:
    items = []
    for key in ORDER:
        it = scan_one(env, key)
        items.append(it)
        if on_item:
            on_item(it)
    return items


def sort_items(items: list[Item]) -> list[Item]:
    """大きい順。無い・取れないものは後ろ。"""
    return sorted(items, key=lambda i: (not i.available, i.size is None, -(i.size or 0)))
