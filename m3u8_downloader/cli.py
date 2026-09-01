"""CLI 入口模块：命令行参数解析与主程序入口."""

import argparse
import sys

from m3u8_downloader import __version__
from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.utils import is_ffmpeg_available, normalize_mp4_filename


def _ensure_utf8_stdout() -> None:
    """冻结后的 EXE 在中文 Windows（GBK 控制台）下，print() 遇到非 GBK 字符
    （如 URL/标题里的 ç、emoji 等）会抛 UnicodeEncodeError 直接崩溃。

    这里把 stdout/stderr 重配置为 utf-8 + errors='replace'，保证任何字符都能
    安全输出（不可编码字符降级为替换符而非崩溃）。在 pytest 的 capsys 环境下
    reconfigure 可能不存在或被跳过，已用 try/except 保护。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


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
        "--from-page",
        action="store_true",
        help="把位置参数 url 当作网页地址，先抽取页内 m3u8 再下载",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="使用无头浏览器深度抽取（隐含 --from-page，需安装 playwright）",
    )
    parser.add_argument(
        "--pick",
        default="",
        help="非交互选择序号：1,3 / 1-3 / 1,3-5 / all（缺省进入交互式输入）",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="只列出候选与估计大小，不下载",
    )
    parser.add_argument(
        "--no-estimate",
        action="store_true",
        help="跳过大小估计（秒出列表，大小/时长显示 -）",
    )
    parser.add_argument(
        "--extract-workers",
        type=int,
        default=8,
        help="抽取/估算并发数 (默认: 8，范围 1-16)",
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
    _ensure_utf8_stdout()
    parser = create_parser()
    args = parser.parse_args()

    # 规范化输出文件名：保证后缀为 .mp4 且只有一个 .mp4，避免 .mp4.mp4 等异常后缀
    args.output = normalize_mp4_filename(args.output)

    # --deep 隐含 --from-page
    if args.deep:
        args.from_page = True

    # 如果指定了 --gui，启动 GUI
    if args.gui:
        from m3u8_downloader.gui import run_gui
        run_gui()
        return

    # 网页抽取模式：把位置参数当作网页地址，先抽取再下载
    if args.from_page:
        _run_from_page(args)
        return

    # CLI 模式下 URL 必填
    if not args.url:
        parser.error("以下参数是必需的: url（或使用 --gui 启动图形界面，或用 --from-page 抽取网页）")

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
        msg = f"\n错误: {e}"
        if ".m3u8" not in args.url.lower():
            msg += "\n提示: 该地址可能不是 m3u8 直链，若它是网页，试试 --from-page"
        print(msg)
        sys.exit(1)
    except Exception as e:
        msg = f"\n未知错误: {e}"
        if ".m3u8" not in args.url.lower():
            msg += "\n提示: 该地址可能不是 m3u8 直链，若它是网页，试试 --from-page"
        print(msg)
        sys.exit(1)


def _print_candidates(candidates: list) -> None:
    """打印候选列表（编号 / 估计大小 / 时长 / 码率 / 类型 / 来源 / 标题 / URL）.

    Args:
        candidates: :func:`extract_m3u8_from_page` 返回的候选列表.
    """
    print(f"{'[序号]':<7}{'估计大小':<12}{'时长':<10}{'码率':<10}{'类型':<8}{'来源':<10} 标题/URL")
    for i, c in enumerate(candidates, 1):
        ctype = "master" if c.is_master else ("-" if not c.reachable else "media")
        title = c.title or "-"
        if not c.reachable:
            title = "(不可达)"
        line = (
            f"[{i}]".ljust(7)
            + f"{c.display_size():<12}"
            + f"{c.display_duration():<10}"
            + f"{c.display_bandwidth():<10}"
            + f"{ctype:<8}"
            + f"{c.source:<10} "
            + title
        )
        print(line)
        print(f"       {c.url}")
    print(f"共 {len(candidates)} 个候选（大小为估计值）")


def _parse_pick(spec: str, total: int) -> list:
    """解析 --pick 序号规格：1,3 / 1-3 / 混合 / all.

    Args:
        spec: 用户输入的规格字符串.
        total: 候选总数（用于越界校验）.

    Returns:
        升序、去重后的 1-based 序号列表.

    Raises:
        ValueError: 规格非法或序号越界.
    """
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(range(1, total + 1))

    out: list = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a_str, b_str = part.split("-", 1)
            a, b = int(a_str), int(b_str)
            if a < 1 or b > total or a > b:
                raise ValueError(f"序号范围越界: {part}（有效 1-{total}）")
            out.extend(range(a, b + 1))
        else:
            n = int(part)
            if n < 1 or n > total:
                raise ValueError(f"序号越界: {n}（有效 1-{total}）")
            out.append(n)

    if not out:
        raise ValueError("未解析到任何有效序号")

    # 升序、去重（便于按顺序串行下载）
    return sorted(set(out))


def _prompt_selection(total: int) -> list:
    """交互式输入要下载的序号（最多重试 3 次）.

    Args:
        total: 候选总数.

    Returns:
        选中的 1-based 序号列表；用户退出（q）或多次无效则返回空列表.
    """
    for _ in range(3):
        try:
            raw = input(
                "请输入序号（如 1 或 1,3 或 1-3，all=全部，q=退出）: "
            ).strip()
        except EOFError:
            return []
        if raw.lower() == "q":
            return []
        try:
            return _parse_pick(raw, total)
        except ValueError as e:
            print(f"输入无效: {e}，请重试")
    print("多次输入无效，已退出")
    return []


def _download_many(selected: list, args) -> None:
    """依次下载选中的候选（串行，文件名自动编号）.

    Args:
        selected: ``(orig_index, Candidate)`` 列表，orig_index 为候选在列表中的序号.
        args: 解析后的命令行参数.
    """
    from m3u8_downloader.downloader import M3U8Downloader
    from m3u8_downloader.utils import (
        build_output_path,
        is_ffmpeg_available,
        normalize_mp4_filename,
    )

    total = len(selected)
    use_ffmpeg = not args.no_ffmpeg
    if use_ffmpeg and not is_ffmpeg_available():
        print("未检测到 ffmpeg，将使用 TS 二进制拼接方式")
        use_ffmpeg = False

    success = 0
    fail = 0
    for orig_idx, c in selected:
        if total <= 1:
            output_path = normalize_mp4_filename(args.output)
        else:
            output_path = build_output_path(args.output, orig_idx, total)
        print(f"\n[{orig_idx}/{total}] 下载: {c.url}")
        print(f"    保存为: {output_path}")
        try:
            downloader = M3U8Downloader(
                url=c.url,
                output=output_path,
                workers=args.workers,
                tmp_dir=args.tmp_dir,
                use_ffmpeg=use_ffmpeg,
                max_retries=args.retries,
                timeout=args.timeout,
            )
            downloader.download()
            success += 1
        except KeyboardInterrupt:
            print("\n用户中断下载")
            sys.exit(130)
        except RuntimeError as e:
            print(f"错误: {e}")
            fail += 1
        except Exception as e:
            print(f"未知错误: {e}")
            fail += 1

    print(f"\n下载完成：成功 {success} 个，失败 {fail} 个")
    if fail > 0:
        sys.exit(1)


def _run_from_page(args) -> None:
    """网页抽取模式主流程：抽取 -> 打印 -> 选择 -> 下载.

    Args:
        args: 解析后的命令行参数（要求 ``args.url`` 为网页地址）.
    """
    page_url = args.url
    if not page_url:
        create_parser().error("使用 --from-page 时必须提供网页 URL 作为位置参数 url")

    deep = bool(args.deep)
    estimate = not bool(args.no_estimate)
    max_workers = max(1, min(int(args.extract_workers or 8), 16))
    timeout = args.timeout

    from m3u8_downloader.extractor import (
        DeepModeUnavailableError,
        NoCandidateFoundError,
        PageFetchError,
        extract_m3u8_from_page,
    )

    try:
        candidates = extract_m3u8_from_page(
            page_url,
            deep=deep,
            timeout=timeout,
            estimate=estimate,
            max_workers=max_workers,
        )
    except DeepModeUnavailableError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except NoCandidateFoundError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except PageFetchError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)

    _print_candidates(candidates)

    if args.list_only:
        return

    # 选择目标
    if args.pick:
        try:
            indices = _parse_pick(args.pick, len(candidates))
        except ValueError as e:
            print(f"错误: {e}")
            sys.exit(2)
    else:
        if not sys.stdin.isatty():
            print("错误: 非交互环境，请使用 --pick 指定要下载的序号（如 --pick 1,3）")
            sys.exit(2)
        indices = _prompt_selection(len(candidates))
        if not indices:
            return

    selected = [(i, candidates[i - 1]) for i in indices]
    _download_many(selected, args)


if __name__ == "__main__":
    main()
