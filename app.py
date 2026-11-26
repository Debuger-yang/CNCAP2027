"""Windows desktop and command-line entry points."""
import argparse
import json
from pathlib import Path
import sys

from cncap2027.engine import calculate, load_config
from cncap2027.output import write_report


def run_desktop():
    from cncap2027.vehicle_window import run_desktop as start_window
    start_window()


def main(argv=None):
    parser = argparse.ArgumentParser(description="C-NCAP 2027 附录 S 灯光计算")
    parser.add_argument("config", nargs="?", help="整车 JSON 配置。无参数打开桌面窗口")
    parser.add_argument("-o", "--output", help="导出 .xlsx 或 .json")
    args = parser.parse_args(argv)
    if args.config is None:
        if args.output:
            parser.error("导出需要提供输入配置")
        run_desktop()
        return 0
    try:
        source = Path(args.config).resolve()
        if args.output and source == Path(args.output).resolve():
            raise ValueError("结果文件不能覆盖输入配置")
        r = calculate(load_config(source), source.parent)
        if args.output:
            write_report(r, args.output)
        else:
            print(json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
