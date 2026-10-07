"""pacman のパッケージファイル名の解析と、pacman と同じ版の比較。

版の比較は libalpm の alpm_pkg_vercmp / rpmvercmp を写したもの。
`vercmp` コマンドがあればそちらを優先する(scan.py 側で選ぶ)。
"""

import re

# name-ver-rel-arch.pkg.tar(.zst|.xz|...)。名前にはハイフンが入るので、
# 後ろの3つ(ver, rel, arch)をハイフンを含まない塊として右から取る。
_PKG_RE = re.compile(
    r"^(?P<name>.+)-(?P<ver>[^-/]+)-(?P<rel>[^-/]+)-(?P<arch>[^-/]+)"
    r"\.pkg\.tar(?:\.[A-Za-z0-9]+)?$"
)


def parse_pkg_filename(filename: str):
    """`name-ver-rel-arch.pkg.tar.zst` を (name, "ver-rel", arch) に。合わなければ None。"""
    m = _PKG_RE.match(filename)
    if not m:
        return None
    return m["name"], f"{m['ver']}-{m['rel']}", m["arch"]


def _isdigit(c: str) -> bool:
    return "0" <= c <= "9"


def _isalpha(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def _isalnum(c: str) -> bool:
    return _isdigit(c) or _isalpha(c)


def rpmvercmp(a: str, b: str) -> int:
    """libalpm の rpmvercmp と同じ規則。-1 / 0 / 1 を返す。"""
    if a == b:
        return 0
    i = j = 0  # one, two
    p1 = p2 = 0  # ptr1, ptr2
    n1, n2 = len(a), len(b)
    while i < n1 and j < n2:
        while i < n1 and not _isalnum(a[i]):
            i += 1
        while j < n2 and not _isalnum(b[j]):
            j += 1
        if i >= n1 or j >= n2:
            break
        # 区切りの長さが違えば、長いほうが新しい
        if (i - p1) != (j - p2):
            return -1 if (i - p1) < (j - p2) else 1
        p1, p2 = i, j
        if _isdigit(a[p1]):
            while p1 < n1 and _isdigit(a[p1]):
                p1 += 1
            while p2 < n2 and _isdigit(b[p2]):
                p2 += 1
            isnum = True
        else:
            while p1 < n1 and _isalpha(a[p1]):
                p1 += 1
            while p2 < n2 and _isalpha(b[p2]):
                p2 += 1
            isnum = False
        seg1, seg2 = a[i:p1], b[j:p2]
        if not seg1:
            return -1  # 起きないはず(libalpm も同じ)
        if not seg2:
            # 数字と英字の比較は数字が新しい
            return 1 if isnum else -1
        if isnum:
            seg1 = seg1.lstrip("0")
            seg2 = seg2.lstrip("0")
            if len(seg1) != len(seg2):
                return 1 if len(seg1) > len(seg2) else -1
        if seg1 != seg2:
            return -1 if seg1 < seg2 else 1
        i, j = p1, p2
    if i >= n1 and j >= n2:
        return 0
    # 残った英字は空に勝たない(1.0a < 1.0)
    rest1 = a[i] if i < n1 else ""
    rest2 = b[j] if j < n2 else ""
    if (not rest1 and not _isalpha(rest2)) or _isalpha(rest1):
        return -1
    return 1


def _parse_evr(evr: str):
    k = 0
    while k < len(evr) and _isdigit(evr[k]):
        k += 1
    dash = evr.rfind("-", k)
    if k < len(evr) and evr[k] == ":":
        epoch = evr[:k] or "0"
        rest_start = k + 1
    else:
        epoch = "0"
        rest_start = 0
    if dash >= 0:
        return epoch, evr[rest_start:dash], evr[dash + 1:]
    return epoch, evr[rest_start:], None


def vercmp(a: str, b: str) -> int:
    """pacman の vercmp と同じ結果。epoch(`1:`)と pkgrel も扱う。"""
    if a == b:
        return 0
    e1, v1, r1 = _parse_evr(a)
    e2, v2, r2 = _parse_evr(b)
    ret = rpmvercmp(e1, e2)
    if ret == 0:
        ret = rpmvercmp(v1, v2)
        if ret == 0 and r1 is not None and r2 is not None:
            ret = rpmvercmp(r1, r2)
    return ret
