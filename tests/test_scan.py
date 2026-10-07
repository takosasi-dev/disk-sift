import json
import os
import unittest
from unittest import mock

from fakefs import FakeFSTest, can_symlink, write
from disksift import pkgver, report, scan

PKG = "var/cache/pacman/pkg"


def make_pacman_cache(t: FakeFSTest):
    d = t.root / PKG
    files = {
        # foo: epoch 付きが一番新しい。消えるのは 1.0-1
        "foo-1.0-1-x86_64.pkg.tar.zst": 100,
        "foo-1.1-1-x86_64.pkg.tar.zst": 110,
        # Windows はファイル名に ':' を使えないので、epoch の代わりに素直な新しい版
        ("foo-1:0.5-1-x86_64.pkg.tar.zst" if os.name != "nt" else "foo-1.2-1-x86_64.pkg.tar.zst"): 120,
        # 名前にハイフン。23.10 > 23.2 > 23.0。消えるのは 23.0
        "python-pip-23.0-1-any.pkg.tar.zst": 200,
        "python-pip-23.10-1-any.pkg.tar.zst": 210,
        "python-pip-23.2-1-any.pkg.tar.zst": 220,
        # 2版以下は残る
        "lib-foo-bar-2.0-1-x86_64.pkg.tar.zst": 300,
        "lib-foo-bar-2.1-1-x86_64.pkg.tar.zst": 310,
    }
    for name, size in files.items():
        write(d / name, size)
        write(d / (name + ".sig"), 10)
    write(d / "download-xyz" / "partial", 5)
    return d


class PacmanCacheTest(FakeFSTest):
    def test_keep_two_newest(self):
        d = make_pacman_cache(self)
        old, npkg = scan.pacman_old_files(self.env())
        self.assertEqual(npkg, 8)
        self.assertEqual(sorted(p.name for p, _ in old), [
            "foo-1.0-1-x86_64.pkg.tar.zst", "foo-1.0-1-x86_64.pkg.tar.zst.sig",
            "python-pip-23.0-1-any.pkg.tar.zst", "python-pip-23.0-1-any.pkg.tar.zst.sig"])
        it = scan.scan_pacman_cache(self.env())
        self.assertEqual(it.reclaim, 100 + 10 + 200 + 10)
        self.assertEqual(it.size, sum(f.stat().st_size for f in d.rglob("*") if f.is_file()))
        self.assertEqual(it.count, 8)
        self.assertEqual([x.label for x in it.details], ["python-pip", "foo"])

    def test_uses_vercmp_command_when_present(self):
        make_pacman_cache(self)
        self.cmds["vercmp"] = lambda a: (0, f"{pkgver.vercmp(a[1], a[2])}\n")
        old, _ = scan.pacman_old_files(self.env())
        self.assertEqual(len(old), 4)
        self.assertTrue(any(c[0] == "vercmp" and len(c) == 3 and c[1] != "1" for c in self.calls))

    def test_missing_is_unavailable(self):
        it = scan.scan_pacman_cache(self.env())
        self.assertFalse(it.available)


class SizeTest(FakeFSTest):
    def test_hardlinks_counted_once(self):
        a = write(self.home / ".cache/x/a", 1000)
        try:
            os.link(a, self.home / ".cache/x/b")
        except OSError:
            self.skipTest("ハードリンクを作れない")
        u = scan.du(self.home / ".cache")
        self.assertEqual((u.size, u.files), (1000, 1))

    def test_symlink_not_followed(self):
        if not can_symlink(self.tmp):
            self.skipTest("シンボリックリンクを作れない")
        outside = write(self.tmp / "outside" / "big", 5000).parent
        write(self.home / ".cache/small", 10)
        os.symlink(outside, self.home / ".cache/link")
        self.assertEqual(scan.du(self.home / ".cache").size, 10)

    def test_unreadable_dir_is_skipped_and_counted(self):
        write(self.home / ".cache/ok/f", 10)
        write(self.home / ".cache/secret/f", 99)
        real = os.listdir
        bad = str(self.home / ".cache" / "secret")

        def listdir(p):
            if str(p) == bad:
                raise PermissionError(13, "Permission denied")
            return real(p)
        with mock.patch.object(scan.os, "listdir", listdir):
            u = scan.du(self.home / ".cache")
        self.assertEqual((u.size, u.skipped), (10, 1))


class ItemsTest(FakeFSTest):
    def test_empty_system_says_none(self):
        items = scan.scan_all(self.env())
        self.assertEqual([i.key for i in items], scan.ORDER)
        by = {i.key: i for i in items}
        for key in ("pacman-cache", "orphans", "journal", "coredump", "user-cache",
                    "trash", "old-logs", "containers"):
            self.assertFalse(by[key].available, key)
            self.assertTrue(by[key].note, key)
        self.assertTrue(by["other"].available)
        self.assertIn("無し", report.text_report(self.env(), items))

    def test_user_cache(self):
        write(self.home / ".cache/pip/http/a", 300)
        write(self.home / ".cache/unknown-app/b", 50)
        write(self.home / ".npm/_cacache/c", 70)
        write(self.home / ".npm/other", 999)  # _cacache の外は数えない
        it = scan.scan_user_cache(self.env())
        self.assertEqual(it.size, 420)
        self.assertEqual([(d.label, d.size) for d in it.details],
                         [(".cache/pip", 300), (".npm/_cacache", 70), (".cache/unknown-app", 50)])
        self.assertIn("pip", it.details[0].note)

    def test_trash(self):
        write(self.home / ".local/share/Trash/files/old.iso", 500)
        write(self.home / ".local/share/Trash/info/old.iso.trashinfo", 20)
        it = scan.scan_trash(self.env())
        self.assertEqual((it.size, it.count), (520, 1))

    def test_coredump(self):
        write(self.root / "var/lib/systemd/coredump/core.app.1000.zst", 4000)
        it = scan.scan_coredump(self.env())
        self.assertEqual((it.size, it.count, it.reclaim), (4000, 1, 4000))

    def test_old_logs(self):
        log = self.root / "var/log"
        write(log / "pacman.log", 1)
        write(log / "Xorg.0.log", 1)
        write(log / "Xorg.0.log.old", 2)
        write(log / "nginx/access.log.1", 3)
        write(log / "nginx/access.log.2.gz", 4)
        write(log / "journal/abc/system@x.journal~", 9999)
        it = scan.scan_old_logs(self.env())
        self.assertEqual((it.size, it.count), (9, 3))

    def test_journal(self):
        self.cmds["journalctl"] = lambda a: (
            0, "Archived and active journals take up 1.5G in the file system.\n")
        it = scan.scan_journal(self.env())
        self.assertEqual(it.size, int(1.5 * 1024 ** 3))
        self.assertEqual(it.reclaim, it.size - 200 * 1024 ** 2)

    def test_journal_without_command_counts_files(self):
        write(self.root / "var/log/journal/m/system.journal", 800)
        it = scan.scan_journal(self.env())
        self.assertEqual((it.size, it.count), (800, 1))
        self.assertIn("journalctl", it.note)

    def test_orphans(self):
        def pacman(a):
            if a[1] == "-Qtdq":
                return 0, "foo\nbar-lib\n"
            return 0, ("Name            : foo\nInstalled Size  : 1.50 MiB\n\n"
                       "Name            : bar-lib\nInstalled Size  : 512.00 KiB\n")
        self.cmds["pacman"] = pacman
        it = scan.scan_orphans(self.env())
        self.assertEqual((it.count, it.size), (2, int(1.5 * 1024 ** 2) + 512 * 1024))
        self.assertFalse(it.cleanable)

    def test_no_orphans(self):
        self.cmds["pacman"] = lambda a: (1, "")
        it = scan.scan_orphans(self.env())
        self.assertEqual((it.available, it.count, it.size), (True, 0, 0))

    def test_containers(self):
        self.cmds["docker"] = lambda a: (0, (
            "TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE\n"
            "Images          5         2         1.5GB     800MB (53%)\n"
            "Containers      3         1         10kB      5kB (50%)\n"
            "Local Volumes   2         1         100MB     0B (0%)\n"
            "Build Cache     0         0         0B        0B\n"))
        it = scan.scan_containers(self.env())
        self.assertEqual(it.size, 1_500_000_000 + 10_000 + 100_000_000)
        self.assertEqual(it.reclaim, 800_000_000 + 5_000)
        self.assertEqual(len(it.details), 4)

    def test_other_excludes_known(self):
        write(self.home / ".cache/x", 1000)
        write(self.home / ".cargo/registry/y", 1000)
        write(self.home / ".cargo/bin/z", 30)
        write(self.home / "Videos/v.mp4", 700)
        it = scan.scan_other(self.env())
        self.assertEqual([(d.label, d.size) for d in it.details], [("Videos", 700), (".cargo", 30)])

    def test_report_and_json(self):
        write(self.home / ".cache/pip/a", 300)
        items = scan.sort_items(scan.scan_all(self.env()))
        self.assertEqual(items[0].key, "user-cache")
        data = json.loads(report.json_report(self.env(), items))
        self.assertEqual(data["items"][0]["size"], 300)
        text = report.text_report(self.env(), items)
        self.assertIn("ユーザーのキャッシュ", text)


class WidthTest(unittest.TestCase):
    def test_width(self):
        self.assertEqual(report.cw("aあ"), 3)
        self.assertEqual(report.fit("あいう", 5), "あい ")
        self.assertEqual(report.rjust("大", 4), "  大")
        self.assertEqual(report.wrap("あいうえ", 4), ["あい", "うえ"])


if __name__ == "__main__":
    unittest.main()
