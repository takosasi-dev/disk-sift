# Changelog

このプロジェクトの変更点を記録します。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に従います。

## [0.1.0] - 2026-10-08

### 追加

- 原因別の一覧: pacman のパッケージキャッシュ、孤立パッケージ、systemd journal、コアダンプ、
  ユーザーのキャッシュ、ゴミ箱、ローテート済みのログ、Docker / Podman、その他(ホームの大きいもの)。
- pacman キャッシュの「各パッケージの新しい2版だけ残す」で空く量の計算。版の比較は `vercmp` があればそれ、
  無ければ同じ規則の Python 実装(epoch も扱う)。
- curses の TUI(`disksift`)、テキスト(`--report`)、JSON(`--json`)。
- `disksift clean <項目>`: 既定は dry-run、`--yes` で消す。決めた場所の外は消さない、
  シンボリックリンクは辿らない、root が要る項目は一般ユーザーでは止める。
