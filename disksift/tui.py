"""curses の画面。中身の計算は scan / clean に任せる。"""

import curses
import locale
import threading

from . import __version__, clean, report, scan
from .report import fit, rjust, wrap
from .scan import human

HELP = {
    "list": "j/k:移動  Enter:詳細  c:掃除  r:再計算  q:終了",
    "detail": "j/k:移動  c:掃除  Esc/h:戻る  q:終了",
    "confirm": "y:実行する  n/Esc:やめる  j/k:スクロール",
    "blocked": "j/k:スクロール  何かキーで戻る",
    "message": "何かキーで一覧に戻る(数え直す)",
}


class App:
    def __init__(self, env: scan.Env):
        self.env = env
        self.items: list[scan.Item] = []
        self.done = 0
        self.scanning = False
        self.mode = "list"
        self.sel = 0  # 一覧の選択
        self.dsel = 0  # 詳細の中身の選択
        self.scroll = 0
        self.plan: clean.Plan | None = None
        self.lines: list[str] = []
        self.flash = ""

    # ---- 数える(別スレッド) ----
    def start_scan(self):
        if self.scanning:
            return
        self.scanning, self.done, self.env.counted = True, 0, 0
        got: list[scan.Item] = []

        def on_item(it):
            got.append(it)
            self.items = scan.sort_items(got)
            self.done = len(got)

        def work():
            try:
                scan.scan_all(self.env, on_item)
            finally:
                self.scanning = False
        self.items = []
        threading.Thread(target=work, daemon=True).start()

    def cur(self) -> scan.Item | None:
        if not self.items:
            return None
        self.sel = max(0, min(self.sel, len(self.items) - 1))
        return self.items[self.sel]

    # ---- 描画 ----
    def put(self, win, y, x, text, attr=0):
        h, w = win.getmaxyx()
        if 0 <= y < h and x < w - 1:
            try:
                win.addstr(y, x, fit(text, w - 1 - x), attr)
            except curses.error:
                pass

    def draw(self, win):
        win.erase()
        h, w = win.getmaxyx()
        self.put(win, 0, 0, f"DiskSift {__version__}  根: {self.env.root}  ホーム: {self.env.home}", curses.A_BOLD)
        body_h = max(1, h - 3)
        getattr(self, "draw_" + ("confirm" if self.mode == "blocked" else self.mode))(win, 1, body_h, w)
        if self.scanning:
            st = (f"数えています… {self.done}/{len(scan.ORDER)}  {self.env.current}"
                  f"  ファイル {self.env.counted:,} 件")
        else:
            st = self.flash
        self.put(win, h - 2, 0, st)
        self.put(win, h - 1, 0, HELP[self.mode], curses.A_REVERSE)
        win.refresh()

    def draw_list(self, win, y0, h, w):
        self.put(win, y0, 0, report.summary_header(), curses.A_UNDERLINE)
        rows = h - 1
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + rows:
            self.scroll = self.sel - rows + 1
        for i, it in enumerate(self.items[self.scroll:self.scroll + rows]):
            idx = self.scroll + i
            attr = curses.A_REVERSE if idx == self.sel else (curses.A_DIM if not it.available else 0)
            self.put(win, y0 + 1 + i, 0, report.summary_row(it), attr)
        if not self.items and self.scanning:
            self.put(win, y0 + 1, 2, "数えています…")

    def detail_lines(self, it: scan.Item, w: int):
        """(文字, 中身の番号 or None) の並び。"""
        out = [(f"{it.title} [{it.key}]  {report.status(it)}", None)]
        if not it.available:
            return out + [(f"無し: {it.note}", None)]
        out.append((f"大きさ {human(it.size)}  件数 {report.count_text(it.count)}"
                    + (f"  空く量 {human(it.reclaim)}" if it.reclaim is not None else ""), None))
        for label, text in (("何なのか", it.about), ("消すと", it.effect), ("掃除", it.how),
                            ("メモ", it.note)):
            if text:
                out += [(s, None) for s in wrap(f"{label}: {text}", max(10, w - 2))]
        if it.skipped:
            out.append((f"読めずに飛ばしたディレクトリ: {it.skipped} 件", None))
        if it.details:
            out.append(("── 中身 ──", None))
            out += [(f"{rjust(human(d.size), 10)}  {d.label}" + (f"  {d.note}" if d.note else ""), i)
                    for i, d in enumerate(it.details)]
        return out

    def draw_detail(self, win, y0, h, w):
        it = self.cur()
        if it is None:
            self.mode = "list"
            return
        lines = self.detail_lines(it, w)
        if it.details:
            self.dsel = max(0, min(self.dsel, len(it.details) - 1))
            pos = next(n for n, (_, i) in enumerate(lines) if i == self.dsel)
            if pos < self.scroll:
                self.scroll = pos
            if pos >= self.scroll + h:
                self.scroll = pos - h + 1
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - h)))
        for n, (text, i) in enumerate(lines[self.scroll:self.scroll + h]):
            attr = curses.A_REVERSE if i is not None and i == self.dsel else 0
            self.put(win, y0 + n, 1, text, attr)

    def draw_confirm(self, win, y0, h, w):
        self.draw_lines(win, y0, h)

    def draw_message(self, win, y0, h, w):
        self.draw_lines(win, y0, h)

    def draw_lines(self, win, y0, h):
        self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - h)))
        for n, text in enumerate(self.lines[self.scroll:self.scroll + h]):
            self.put(win, y0 + n, 1, text)

    # ---- 掃除 ----
    def open_clean(self):
        it = self.cur()
        if it is None:
            return
        if self.scanning:
            self.flash = "数え終わってから掃除してください"
            return
        only = ()
        if self.mode == "detail" and it.key == "user-cache" and it.details:
            only = (it.details[self.dsel].label,)
        try:
            self.plan = clean.make_plan(self.env, it.key, only)
        except clean.PlanError as e:
            self.flash = str(e)
            return
        p = self.plan
        head = [f"掃除の確認: {p.title}" + (f"({only[0]} だけ)" if only else ""), ""]
        self.lines = head + report.plan_lines(p)
        if p.blocked:
            self.mode = "blocked"
        elif not p.entries and not p.command:
            self.flash = "消すものはありません"
            return
        else:
            self.lines += ["", "本当に消しますか? y で実行、n でやめる"]
            self.mode = "confirm"
        self.scroll = 0

    def run_clean(self, win):
        self.lines = ["消しています…"]
        self.mode = "message"
        self.draw(win)
        try:
            res = clean.execute(self.env, self.plan)
            self.lines = report.result_lines(self.plan, res)
        except clean.PlanError as e:
            self.lines = [f"実行しなかった: {e}"]
        self.scroll = 0

    # ---- キー ----
    def key(self, ch, win) -> bool:
        """False で終わる。"""
        m = self.mode
        down = ch in (ord("j"), curses.KEY_DOWN)
        up = ch in (ord("k"), curses.KEY_UP)
        back = ch in (27, ord("h"), curses.KEY_LEFT)
        self.flash = "" if ch != -1 else self.flash
        if m == "list":
            if ch == ord("q"):
                return False
            if down:
                self.sel += 1
            elif up:
                self.sel = max(0, self.sel - 1)
            elif ch in (10, 13, curses.KEY_ENTER, ord("l"), curses.KEY_RIGHT):
                if self.cur():
                    self.mode, self.dsel, self.scroll = "detail", 0, 0
            elif ch == ord("c"):
                self.open_clean()
            elif ch == ord("r"):
                self.start_scan()
        elif m == "detail":
            if ch == ord("q"):
                return False
            if back:
                self.mode, self.scroll = "list", 0
            elif down:
                self.dsel += 1
                if not self.cur().details:
                    self.scroll += 1
            elif up:
                self.dsel = max(0, self.dsel - 1)
                if not self.cur().details:
                    self.scroll = max(0, self.scroll - 1)
            elif ch == ord("c"):
                self.open_clean()
        elif m in ("confirm", "blocked"):
            if down:
                self.scroll += 1
            elif up:
                self.scroll = max(0, self.scroll - 1)
            elif m == "confirm" and ch == ord("y"):
                self.run_clean(win)
            elif ch != -1 and (m == "blocked" or ch in (ord("n"), 27, ord("q"))):
                self.mode, self.scroll = "list", 0
        elif m == "message" and ch != -1:
            self.mode, self.scroll = "list", 0
            self.start_scan()
        return True


def main(win, env) -> int:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    win.timeout(150)
    app = App(env)
    app.start_scan()
    while True:
        app.draw(win)
        if not app.key(win.getch(), win):
            return 0


def run(env: scan.Env) -> int:
    locale.setlocale(locale.LC_ALL, "")  # これが無いと全角が化ける
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)  # Esc で戻るのを待たせない
    return curses.wrapper(main, env)
