import contextlib
import io
import os
import unittest

from fakefs import FakeFSTest, can_symlink, write
from disksift import __main__ as cli
from disksift import clean
from test_scan import PKG, make_pacman_cache


class CleanTest(FakeFSTest):
    def test_pacman_cache_deletes_old_and_sig_only(self):
        d = make_pacman_cache(self)
        before = {p.name for p in d.iterdir()}
        plan = clean.make_plan(self.env(), "pacman-cache")
        self.assertEqual(plan.total, 320)
        res = clean.execute(self.env(), plan)
        self.assertEqual((res.deleted, res.freed, res.failed), (4, 320, []))
        after = {p.name for p in d.iterdir()}
        self.assertEqual(before - after, {
            "foo-1.0-1-x86_64.pkg.tar.zst", "foo-1.0-1-x86_64.pkg.tar.zst.sig",
            "python-pip-23.0-1-any.pkg.tar.zst", "python-pip-23.0-1-any.pkg.tar.zst.sig"})

    def test_root_required_blocks(self):
        make_pacman_cache(self)
        n = len(list((self.root / PKG).iterdir()))
        plan = clean.make_plan(self.env(euid=1000), "pacman-cache")
        self.assertIn("root が要る", plan.blocked)
        with self.assertRaises(clean.PlanError):
            clean.execute(self.env(euid=1000), plan)
        self.assertEqual(len(list((self.root / PKG).iterdir())), n)

    def test_user_items_do_not_need_root(self):
        write(self.home / ".cache/pip/a", 10)
        self.assertEqual(clean.make_plan(self.env(euid=1000), "user-cache").blocked, "")

    def test_user_cache_only(self):
        write(self.home / ".cache/pip/a", 10)
        write(self.home / ".cache/mozilla/b", 20)
        plan = clean.make_plan(self.env(), "user-cache", only=("pip",))
        res = clean.execute(self.env(), plan)
        self.assertEqual(res.freed, 10)
        self.assertFalse((self.home / ".cache/pip").exists())
        self.assertTrue((self.home / ".cache/mozilla/b").exists())
        self.assertTrue((self.home / ".cache").is_dir())

    def test_trash_keeps_structure(self):
        t = self.home / ".local/share/Trash"
        write(t / "files/dir/x", 5)
        write(t / "info/dir.trashinfo", 1)
        res = clean.execute(self.env(), clean.make_plan(self.env(), "trash"))
        self.assertEqual(res.deleted, 2)
        self.assertEqual(list((t / "files").iterdir()), [])
        self.assertTrue((t / "info").is_dir())

    def test_old_logs_only_rotated(self):
        log = self.root / "var/log"
        write(log / "pacman.log", 1)
        write(log / "x.log.1", 1)
        write(log / "journal/m/system.journal.1", 1)  # journal の下は対象外
        clean.execute(self.env(), clean.make_plan(self.env(), "old-logs"))
        self.assertTrue((log / "pacman.log").exists())
        self.assertFalse((log / "x.log.1").exists())
        self.assertTrue((log / "journal/m/system.journal.1").exists())

    def test_coredump(self):
        write(self.root / "var/lib/systemd/coredump/core.a.zst", 9)
        res = clean.execute(self.env(), clean.make_plan(self.env(), "coredump"))
        self.assertEqual(res.freed, 9)
        self.assertTrue((self.root / "var/lib/systemd/coredump").is_dir())

    def test_journal_runs_vacuum(self):
        self.cmds["journalctl"] = lambda a: (0, "Archived and active journals take up 1G in the file system.\n")
        plan = clean.make_plan(self.env(), "journal", vacuum_size="500M")
        self.assertEqual(plan.command, ["journalctl", "--vacuum-size=500M"])
        self.assertEqual(plan.total, 1024 ** 3 - 500 * 1024 ** 2)
        clean.execute(self.env(), plan)
        self.assertIn(["journalctl", "--vacuum-size=500M"], self.calls)

    def test_journal_bad_size(self):
        with self.assertRaises(clean.PlanError):
            clean.make_plan(self.env(), "journal", vacuum_size="200M; rm -rf /")

    def test_guide_only_items(self):
        for key in ("orphans", "containers", "other"):
            plan = clean.make_plan(self.env(), key)
            self.assertTrue(plan.blocked and plan.guide, key)
            with self.assertRaises(clean.PlanError):
                clean.execute(self.env(), plan)

    def test_outside_roots_refused(self):
        victim = write(self.tmp / "precious", 1)
        plan = clean.Plan("user-cache", "x", entries=[(victim, 1)],
                          roots=[self.home / ".cache"])
        res = clean.execute(self.env(), plan)
        self.assertEqual(res.deleted, 0)
        self.assertTrue(victim.exists())
        # 根そのものも消さない
        (self.home / ".cache").mkdir()
        plan.entries = [(self.home / ".cache", 0)]
        clean.execute(self.env(), plan)
        self.assertTrue((self.home / ".cache").is_dir())

    def test_symlinks_not_followed(self):
        if not can_symlink(self.tmp):
            self.skipTest("シンボリックリンクを作れない")
        outside = self.tmp / "outside"
        write(outside / "keep", 3)
        write(self.home / ".cache/real/a", 1)
        os.symlink(outside, self.home / ".cache/link")
        # リンク経由で根の外を指すパスは消さない
        plan = clean.Plan("user-cache", "x", entries=[(self.home / ".cache/link/keep", 3)],
                          roots=[self.home / ".cache"])
        self.assertEqual(clean.execute(self.env(), plan).deleted, 0)
        self.assertTrue((outside / "keep").exists())
        # リンクそのものは消えるが、リンク先は残る
        res = clean.execute(self.env(), clean.make_plan(self.env(), "user-cache"))
        self.assertEqual(res.failed, [])
        self.assertFalse(os.path.lexists(self.home / ".cache/link"))
        self.assertTrue((outside / "keep").exists())


class CliTest(FakeFSTest):
    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cli.main([*args, "--root", str(self.root), "--home", str(self.home)])
        return rc, out.getvalue()

    def test_dry_run_by_default(self):
        f = write(self.home / ".cache/pip/a", 10)
        rc, out = self.cli("clean", "user-cache")
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out)
        self.assertIn(str(self.home / ".cache" / "pip"), out)
        self.assertTrue(f.exists())
        rc, out = self.cli("clean", "user-cache", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertFalse(f.exists())

    def test_guide_exit_code(self):
        rc, out = self.cli("clean", "orphans", "--yes")
        self.assertEqual(rc, 2)
        self.assertIn("pacman -Rns", out)

    def test_unknown_item(self):
        rc, out = self.cli("clean", "nope")
        self.assertEqual(rc, 2)

    def test_report_and_json(self):
        write(self.home / "Music/a.flac", 10)
        rc, out = self.cli("--report")
        self.assertEqual(rc, 0)
        self.assertIn("その他", out)
        rc, out = self.cli("--json")
        self.assertIn('"key": "other"', out)


if __name__ == "__main__":
    unittest.main()
