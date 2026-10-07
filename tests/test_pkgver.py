import itertools
import shutil
import subprocess
import unittest

import fakefs  # noqa: F401  (sys.path を通す)
from disksift.pkgver import parse_pkg_filename, vercmp

# pacman の test/util/vercmptest.sh と同じ組
CASES = [
    ("1.5.0", "1.5.0", 0), ("1.5.1", "1.5.0", 1), ("1.5.1", "1.5", 1),
    ("1.5.0-1", "1.5.0-1", 0), ("1.5.0-1", "1.5.0-2", -1), ("1.5.0-1", "1.5.1-1", -1),
    ("1.5.0-2", "1.5.1-1", -1), ("1.5-1", "1.5.1-1", -1), ("1.5-2", "1.5.1-1", -1),
    ("1.5-2", "1.5.1-2", -1), ("1.5", "1.5-1", 0), ("1.5-1", "1.5", 0),
    ("1.1-1", "1.1", 0), ("1.0-1", "1.1", -1), ("1.1-1", "1.0", 1),
    ("1.5b-1", "1.5-1", -1), ("1.5b", "1.5", -1), ("1.5b-1", "1.5", -1), ("1.5b", "1.5.1", -1),
    ("1.0a", "1.0alpha", -1), ("1.0alpha", "1.0b", -1), ("1.0b", "1.0beta", -1),
    ("1.0beta", "1.0rc", -1), ("1.0rc", "1.0", -1),
    ("1.5.a", "1.5", 1), ("1.5.b", "1.5.a", 1), ("1.5.1", "1.5.b", 1),
    ("1.5.b-1", "1.5.b", 0), ("1.5-1", "1.5.b", -1),
    ("2.0", "2_0", 0), ("2.0_a", "2_0.a", 0), ("2.0a", "2.0.a", -1), ("2___a", "2_a", 1),
    ("0:1.0", "0:1.0", 0), ("0:1.0", "0:1.1", -1), ("1:1.0", "0:1.0", 1),
    ("1:1.0", "0:1.1", 1), ("1:1.0", "2:1.1", -1), ("1:1.0", "0:1.0-1", 1),
    ("1:1.0-1", "0:1.1-1", 1), ("0:1.0", "1.0", 0), ("0:1.0", "1.1", -1),
    ("0:1.1", "1.0", 1), ("1:1.0", "1.0", 1), ("1:1.0", "1.1", 1), ("1:1.1", "1.1", 1),
]

EXTRA = ["1.0", "1.0.0", "1.00", "01.0", "1.0-10", "1.0-9", "1.0+r1", "1.0.r1.gabc",
         "2:0.1-1", "1.0rc1", "1.0~rc1", "20240101-1", "1.2.3a", "a1", "1..0", "1.0."]


class VercmpTest(unittest.TestCase):
    def test_pacman_cases(self):
        for a, b, want in CASES:
            with self.subTest(a=a, b=b):
                self.assertEqual(vercmp(a, b), want)
                self.assertEqual(vercmp(b, a), -want)

    @unittest.skipUnless(shutil.which("vercmp"), "vercmp コマンドが無い")
    def test_same_as_real_vercmp(self):
        words = sorted({v for c in CASES for v in c[:2]} | set(EXTRA))
        for a, b in itertools.combinations(words, 2):
            out = subprocess.run(["vercmp", a, b], capture_output=True, text=True).stdout
            real = int(out.strip())
            with self.subTest(a=a, b=b):
                self.assertEqual(vercmp(a, b), (real > 0) - (real < 0))


class ParseTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_pkg_filename("lib32-gcc-libs-14.2.1+r730-1-x86_64.pkg.tar.zst"),
                         ("lib32-gcc-libs", "14.2.1+r730-1", "x86_64"))
        self.assertEqual(parse_pkg_filename("foo-1:2.0-3-any.pkg.tar.xz"), ("foo", "1:2.0-3", "any"))
        self.assertEqual(parse_pkg_filename("a-b-c-1-2-x86_64.pkg.tar"), ("a-b-c", "1-2", "x86_64"))
        self.assertIsNone(parse_pkg_filename("foo-1.0-1-x86_64.pkg.tar.zst.sig"))
        self.assertIsNone(parse_pkg_filename("foo.pkg.tar.zst"))
        self.assertIsNone(parse_pkg_filename("download-abc123"))


if __name__ == "__main__":
    unittest.main()
