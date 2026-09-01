"""CLI 入口模块：命令行参数解析与主程序入口."""

import argparse
import sys

from m3u8_downloader import __version__
from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.utils import is_ffmpeg_available, normalize_mp4_filename


def create_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器.

    Returns:
        配置好的 ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="m3u8-dl",
        description="m3u8 下载工具 - 下载 m3u8 流并转换为 MP4 文件",
        epilog="示例: m3u8-dl https://example.com/index.m3u8 -o video.mp4",
    )

    parser.add_argument(
        "url",
        nargs="?",
        help="m3u8 播放列表 URL（使用 --gui 时可省略）",
    )
    parser.add_argument(
        "-o", "--output",
        default="output.mp4",
        help="输出文件路径 (默认: output.mp4)",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=8,
        help="并发下载线程数 (默认: 8)",
    )
    parser.add_argument(
        "--tmp-dir",
        default="",
        help="临时文件目录 (默认: 输出文件同目录下的 .tmp)",
    )
    parser.add_argument(
        "--no-ffmpeg",
        action="store_true",
        help="不使用 ffmpeg 合并转码（仅做 TS 二进制拼接）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="下载失败重试次数 (默认: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP 请求超时时间/秒 (默认: 30)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动图形界面",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"m3u8-dl {__version__}",
    )

    return parser


def main() -> None:
    """CLI 主入口函数."""
    parser = create_parser()
    args = parser.parse_args()

    # 规范化输出文件名：保证后缀为 .mp4 且只有一个 .mp4，避免 .mp4.mp4 等异常后缀
    args.output = normalize_mp4_filename(args.output)

    # 如果指定了 --gui，启动 GUI
    if args.gui:
        from m3u8_downloader.gui import run_gui
        run_gui()
        return

    # CLI 模式下 URL 必填
    if not args.url:
        parser.error("以下参数是必需的: url（或使用 --gui 启动图形界面）")

    # 参数校验
    if not args.url.startswith(("http://", "https://")):
        print(f"错误: URL 必须以 http:// 或 https:// 开头，当前输入: {args.url}")
        sys.exit(1)

    if args.workers < 1:
        print(f"错误: 并发线程数必须 >= 1，当前输入: {args.workers}")
        sys.exit(1)

    if args.retries < 0:
        print(f"错误: 重试次数不能为负数，当前输入: {args.retries}")
        sys.exit(1)

    # 检查 ffmpeg
    use_ffmpeg = not args.no_ffmpeg
    if use_ffmpeg:
        if is_ffmpeg_available():
            print("检测到 ffmpeg，将使用 ffmpeg 转码")
        else:
            print("未检测到 ffmpeg，将使用 TS 二进制拼接方式")
            print("提示: 安装 ffmpeg 可获得更好的转码质量 (https://ffmpeg.org)")
            use_ffmpeg = False

    # 创建下载器并执行
    try:
        downloader = M3U8Downloader(
            url=args.url,
            output=args.output,
            workers=args.workers,
            tmp_dir=args.tmp_dir,
            use_ffmpeg=use_ffmpeg,
            max_retries=args.retries,
            timeout=args.timeout,
        )
        downloader.download()
    except KeyboardInterrupt:
        print("\n用户中断下载")
        sys.exit(130)
    except RuntimeError as e:
        print(f"\n错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n未知错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
