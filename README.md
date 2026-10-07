# DiskSift

何がディスクを食っているかを**原因別**に出し、安全に消せるものはその場で掃除する道具です。
ncdu のようにディレクトリを大きさ順に並べるのではなく、「pacman のキャッシュ」「journal」
「ユーザーのキャッシュ」のように原因ごとにまとめ、それぞれ「何なのか」「消すとどうなるか」
「どう掃除するか」を添えて出します。

Arch Linux を主な対象にしていますが、ほかのディストリでも、無い項目は「無し」と出るだけで動きます。

## 出す項目

| 項目名 | 内容 | 掃除 |
|---|---|---|
| `pacman-cache` | pacman のパッケージキャッシュ(`/var/cache/pacman/pkg`)。各パッケージの新しい2版だけ残したときに空く量も出す | 古い版と対の `.sig` を消す(root) |
| `orphans` | 孤立パッケージ(`pacman -Qtdq`)と大きさ | 案内だけ |
| `journal` | systemd journal(`journalctl --disk-usage`) | `journalctl --vacuum-size=<N>`(既定 200M、root) |
| `coredump` | コアダンプ(`/var/lib/systemd/coredump`) | 中身を消す(root) |
| `user-cache` | `~/.cache` の中身(pip, yay, paru, go-build, mozilla, chromium などは説明つき)、`~/.npm/_cacache`、`~/.cargo/registry`、`~/.gradle/caches` | 全部、または `--only` で選んだものを消す |
| `trash` | ゴミ箱(`~/.local/share/Trash`) | 中身を消す |
| `old-logs` | `/var/log` のローテート済みのログ(`*.gz` `*.old` `*.数字`。journal は除く) | 当てはまるファイルを消す(root) |
| `containers` | Docker / Podman の `system df` | 案内だけ |
| `other` | 上のどれにも入らない、ホームの大きいもの | 案内だけ |

pacman の版の比較は、`vercmp` コマンドがあればそれを使い、無ければ同じ規則(epoch `1:` を含む)の
Python 実装を使います。

## 画面

```
DiskSift 0.1.0  根: /  ホーム: /home/user
    大きさ      件数  項目                            掃除
   3.2 GiB     1,204  pacman のパッケージキャッシュ   掃除可(root)  空く量 1.9 GiB
   2.1 GiB    48,310  ユーザーのキャッシュ            掃除可
   1.4 GiB    12,877  その他(ホームの大きいもの)      案内のみ
 512.0 MiB        14  systemd journal                 掃除可(root)  空く量 312.0 MiB
 120.5 MiB         9  孤立パッケージ                  案内のみ
  64.0 MiB         3  ゴミ箱                          掃除可
       0 B         0  コアダンプ                      掃除可(root)
         -         -  Docker / Podman                 無し

j/k:移動  Enter:詳細  c:掃除  r:再計算  q:終了
```

Enter で詳細(説明と中身の一覧)、c で掃除の確認画面(消すファイルの一覧か実行するコマンドと、
合計の大きさ)を出し、y を押したときだけ消します。ユーザーのキャッシュは、詳細画面で選んだものだけを
c で消せます。

## 動作環境

- Linux(Arch Linux で確認)
- Python 3.11 以上
- 依存なし(標準ライブラリだけ。画面は curses)

## 入れ方

```sh
pipx install git+https://github.com/takosasi-dev/disk-sift
```

または clone して、そのまま動かします。

```sh
git clone https://github.com/takosasi-dev/disk-sift
cd disk-sift
python -m disksift
```

## 使い方

```sh
disksift                       # TUI
disksift --report              # テキストで一覧と詳細
disksift --json                # JSON
disksift clean user-cache      # 消すものの一覧を見るだけ(dry-run)
disksift clean user-cache --yes
disksift clean user-cache --only pip --only yay --yes
sudo disksift clean pacman-cache --yes
sudo disksift clean journal --vacuum-size 500M --yes
```

`--root` と `--home` で見る場所を差し替えられます(`--root` を `/` 以外にすると、手元のシステムの
結果が混ざらないよう外部コマンドは使いません)。

## 安全面の注意

- 実際に消すのは `pacman-cache` `journal` `coredump` `user-cache` `trash` `old-logs` だけです。
  `orphans` `containers` `other` はコマンドを案内するだけで、消しません。
- `clean` は既定で dry-run です。`--yes` を付けたときだけ消します。TUI は確認画面で y を押したときだけ消します。
- 消す直前に、実パスが決めた場所(例: `~/.cache`)の下にあるかを確かめ、外なら消しません。
- シンボリックリンクは辿りません。リンクそのものは消えますが、リンク先には触りません。
- root が要る項目を一般ユーザーで実行すると、消さずに「root が要る」と出します。
  `sudo` で動かしたときは、ユーザーのキャッシュとゴミ箱は呼んだ人のホームを見ます。
- 大きさはファイルの見かけの大きさ(`st_size`)の合計です。ハードリンクは1回だけ数え、
  読めないディレクトリは飛ばしてその数を出します。

## 開発

```sh
python tests/run_all.py
```

テストは一時ディレクトリに作った偽のファイルシステムで動き、掃除のテストもその中だけで消します。

## ライセンス

MIT

## 開発状況

v0.1.0
