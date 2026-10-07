"""テスト用の偽のファイルシステム(一時ディレクトリ)と偽のコマンド。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from disksift.scan import Env  # noqa: E402


def write(path: Path, size: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def can_symlink(tmp: Path) -> bool:
    try:
        os.symlink(tmp / "nowhere", tmp / ".symtest")
    except (OSError, NotImplementedError):
        return False
    os.unlink(tmp / ".symtest")
    return True


class FakeFSTest(unittest.TestCase):
    """self.root(/ の代わり)と self.home を一時ディレクトリに作る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "root"
        self.home = self.root / "home" / "user"
        self.home.mkdir(parents=True)
        self.cmds = {}  # コマンド名 -> 関数(args) -> (rc, stdout)
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def run_cmd(self, args):
        self.calls.append(list(args))
        f = self.cmds.get(args[0])
        return f(args) if f else None

    def env(self, euid: int = 0) -> Env:
        return Env(root=self.root, home=self.home, euid=euid, run=self.run_cmd)
