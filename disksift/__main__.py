"""disksift コマンドの入口。

    disksift                    TUI
    disksift --report | --json  画面なしで一覧
    disksift clean <項目> [--yes]
"""

import argparse
import sys

from . import __version__, clean, report, scan


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="システムの根(既定 /)。/ 以外にすると外部コマンドは使わない")
    common.add_argument("--home", default=argparse.SUPPRESS, help="ホーム(既定 $HOME。sudo なら呼んだ人のホーム)")

    p = argparse.ArgumentParser(
        prog="disksift", parents=[common],
        description="何がディスクを食っているかを原因別に出し、安全に消せるものは掃除する。")
    p.add_argument("--version", action="version", version=f"disksift {__version__}")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--report", action="store_true", help="テキストで一覧を出す")
    out.add_argument("--json", action="store_true", help="JSON で一覧を出す")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("clean", parents=[common], help="項目を掃除する(既定は dry-run)",
                       description="既定は確認だけ(dry-run)。--yes を付けたときだけ消す。")
    c.add_argument("item", help=f"項目名: {', '.join(scan.ORDER)}")
    c.add_argument("--yes", action="store_true", help="本当に消す")
    c.add_argument("--only", action="append", default=[],
                   help="user-cache のうちこれだけ消す(例: pip、.cache/pip。何回でも)")
    c.add_argument("--vacuum-size", default=scan.DEFAULT_VACUUM,
                   help=f"journal をこの大きさまで減らす(既定 {scan.DEFAULT_VACUUM})")
    return p


def cmd_clean(env: scan.Env, args) -> int:
    try:
        plan = clean.make_plan(env, args.item, tuple(args.only), args.vacuum_size)
    except clean.PlanError as e:
        print(f"disksift: {e}", file=sys.stderr)
        return 2
    print(f"掃除の予定: {plan.title} [{plan.key}]")
    print("\n".join(report.plan_lines(plan)))
    if plan.guide:
        return 2
    if not args.yes:
        if not plan.blocked:
            print("\nこれは確認だけです(dry-run)。消すには --yes を付けて実行してください。")
        return 1 if plan.blocked else 0
    if plan.blocked:
        return 1
    if not plan.entries and not plan.command:
        print("消すものはありません。")
        return 0
    res = clean.execute(env, plan)
    print("\n".join(report.result_lines(plan, res)))
    return 1 if res.failed else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    env = scan.default_env(getattr(args, "root", None), getattr(args, "home", None))
    if args.cmd == "clean":
        return cmd_clean(env, args)
    if args.report or args.json:
        items = scan.sort_items(scan.scan_all(env))
        print(report.json_report(env, items) if args.json else report.text_report(env, items), end="")
        return 0
    from . import tui
    return tui.run(env)


if __name__ == "__main__":
    sys.exit(main())
