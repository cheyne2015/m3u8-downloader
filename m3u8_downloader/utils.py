"""工具函数模块：进度条、文件大小格式化、时间格式化、HTTP Session 管理."""

import os
import sys
import time
from typing import Optional

import requests


def format_file_size(size_bytes: float) -> str:
    """将字节数格式化为人类可读的文件大小字符串.

    Args:
        size_bytes: 字节数.

    Returns:
        格式化后的文件大小字符串，如 "1.23 MB".
    """
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def format_duration(seconds: float) -> str:
    """将秒数格式化为人类可读的时间字符串.

    Args:
        seconds: 秒数.

    Returns:
        格式化后的时间字符串，如 "01:23:45".
    """
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_speed(speed_bytes_per_sec: float) -> str:
    """格式化下载速度.

    Args:
        speed_bytes_per_sec: 每秒字节数.

    Returns:
        格式化后的速度字符串，如 "1.23 MB/s".
    """
    return f"{format_file_size(speed_bytes_per_sec)}/s"


class ProgressBar:
    """简单的命令行进度条（tqdm 风格）.

    当 tqdm 可用时使用 tqdm，否则使用内置简单实现。
    """

    def __init__(
        self,
        total: int,
        desc: str = "",
        unit: str = "个",
        disable: bool = False,
    ) -> None:
        """初始化进度条.

        Args:
            total: 总数量.
            desc: 描述前缀.
            unit: 单位名称.
            disable: 是否禁用进度条.
        """
        self._total = total
        self._desc = desc
        self._unit = unit
        self._disable = disable
        self._count = 0
        self._start_time = time.time()
        self._last_update_time = 0.0

        try:
            from tqdm import tqdm  # noqa: F811

            self._tqdm = tqdm(
                total=total,
                desc=desc,
                unit=unit,
                disable=disable,
                ncols=80,
            )
        except ImportError:
            self._tqdm = None

    def update(self, n: int = 1) -> None:
        """更新进度.

        Args:
            n: 增加的数量.
        """
        self._count += n
        if self._tqdm is not None:
            self._tqdm.update(n)
        elif not self._disable:
            now = time.time()
            # 每 0.5 秒刷新一次，避免输出过频
            if now - self._last_update_time < 0.5 and self._count < self._total:
                return
            self._last_update_time = now
            elapsed = now - self._start_time
            speed = self._count / elapsed if elapsed > 0 else 0
            eta = (self._total - self._count) / speed if speed > 0 else 0
            percent = self._count / self._total * 100 if self._total > 0 else 0

            bar_width = 30
            filled = int(bar_width * self._count / self._total) if self._total > 0 else 0
            bar = "█" * filled + "░" * (bar_width - filled)

            line = (
                f"\r{self._desc} |{bar}| {percent:.1f}% "
                f"{self._count}/{self._total}{self._unit} "
                f"[{format_speed(speed)} ETA {format_duration(eta)}]"
            )
            sys.stderr.write(line)
            if self._count >= self._total:
                sys.stderr.write("\n")
            sys.stderr.flush()

    def close(self) -> None:
        """关闭进度条."""
        if self._tqdm is not None:
            self._tqdm.close()
        elif not self._disable and self._count < self._total:
            sys.stderr.write("\n")


def create_http_session(
    timeout: int = 30,
    headers: Optional[dict] = None,
    no_proxy: bool = False,
    proxy: Optional[str] = None,
) -> requests.Session:
    """创建带默认配置的 HTTP Session.

    Args:
        timeout: 默认超时时间（秒）.
        headers: 自定义请求头.
        no_proxy: 为 True 时绕过系统代理环境变量（HTTPS_PROXY / HTTP_PROXY /
            ALL_PROXY 等），所有请求走直连。内部设置 ``session.trust_env = False``
            以禁用环境代理读取（不影响其他 session），不会改动全局 ``os.environ``。
        proxy: 手动指定代理地址（如 ``127.0.0.1:7897``）。非空时自动补全协议头，
            同时作用于 http 与 https。与 ``no_proxy=True`` 互斥，二者同时传入时
            ``no_proxy`` 优先生效（直连）。

    Returns:
        配置好的 requests.Session.
    """
    session = requests.Session()
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if headers:
        default_headers.update(headers)
    session.headers.update(default_headers)
    # 将 timeout 绑定到 session 上供后续使用
    session._default_timeout = timeout  # type: ignore[attr-defined]
    # 直连/跳过代理：关闭 session 对环境代理（HTTPS_PROXY/HTTP_PROXY 等）的读取，
    # 所有请求走直连。注意 proxies={"http": None} 在 requests 2.34 会被环境代理覆盖，
    # 必须用 trust_env=False 才能真正绕过。
    if no_proxy:
        session.trust_env = False
    elif proxy:
        proxy_url = _normalize_proxy(proxy)
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return session


def _normalize_proxy(proxy: str) -> str:
    """把用户输入的代理地址规范化为带协议头的完整 URL.

    接受多种形式：
    - ``127.0.0.1:7897`` -> ``http://127.0.0.1:7897``
    - ``socks5://127.0.0.1:7897`` -> 原样
    - ``http://user:pass@host:port`` -> 原样

    Args:
        proxy: 用户输入的代理字符串。

    Returns:
        带协议头的代理 URL；空输入返回空字符串。
    """
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if proxy.startswith(("http://", "https://", "socks5://", "socks5h://", "socks4://")):
        return proxy
    return "http://" + proxy


# Windows 文件名保留字符（非法，不能出现在文件名中）
_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def sanitize_filename_component(name: str, max_component_len: int = 180) -> str:
    """清洗单个文件名片段中的 Windows 非法字符并限制长度.

    仅针对「文件名主体」使用，不要传入含目录分隔符的完整路径。
    - 将保留字符 ``< > : " / \\ | ? *`` 替换为下划线 ``_``；
    - 删除控制字符（``ord(ch) < 32``）；
    - 去除首尾空格与结尾的句点（Windows 不允许文件/目录名以 ``.`` 或空格结尾）；
    - 超过 ``max_component_len`` 时截断（并再次清理结尾的 ``.``/空格），
      为完整路径预留目录与 ``.mp4`` 后缀空间，避免超出 MAX_PATH(260)。

    Args:
        name: 待清洗的文件名片段（不含目录）。
        max_component_len: 文件名主体最大长度，默认 180。

    Returns:
        清洗后的安全文件名片段；若清洗后为空则返回 ``"output"``。
    """
    if not name:
        return "output"
    cleaned_chars = []
    for ch in name:
        if ch in _INVALID_FILENAME_CHARS:
            cleaned_chars.append("_")
        elif ord(ch) < 32:
            # 跳过控制字符
            continue
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars).strip().rstrip(".")
    if not cleaned:
        return "output"
    if len(cleaned) > max_component_len:
        cleaned = cleaned[:max_component_len].rstrip(". ")
    return cleaned


def extract_title_segment(title: str) -> str:
    """从网页标题截取「第一个 '-' 之前的段落」作为输出文件名基底.

    规则（贴近用户示例）：
    - 优先按 ``" - "``（空格-连字符-空格）切分，取第一段并去首尾空白；
    - 若不存在 ``" - "`` 但存在单个 ``"-"``，则按 ``"-"`` 切分取第一段；
    - 若整个标题不含 ``"-"``，则原样返回（去首尾空白）；
    - 结果为空时返回空字符串，由调用方回退默认名。

    示例：
        ``"仙界法务部 第55集 (2026) - 动漫 - 在线免费观看 - 冷映"``
        -> ``"仙界法务部 第55集 (2026)"``

    Args:
        title: 网页 ``<title>`` 或 og:title 文本。

    Returns:
        截取后的文件名基底（不含扩展名）；无法截取时返回空字符串。
    """
    if not title:
        return ""
    text = title.strip()
    if not text:
        return ""
    if " - " in text:
        seg = text.split(" - ")[0].strip()
    elif "-" in text:
        seg = text.split("-")[0].strip()
    else:
        seg = text
    if not seg:
        return ""
    return sanitize_filename_component(seg)


def is_ffmpeg_available() -> bool:
    """检测系统是否安装了 ffmpeg.

    Returns:
        True 如果 ffmpeg 可用.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def normalize_mp4_filename(name: str) -> str:
    """将任意文件名规范化为以且仅以一个 .mp4 结尾的路径.

    无论用户填写什么（无后缀 / 其他后缀 / 多个 .mp4），最终都保证：
    1. 文件后缀为 .mp4；
    2. 不会出现 .mp4.mp4 之类的重复后缀（字符串末尾只有唯一的 .mp4）。

    规则：
    - 反复去掉末尾的 .mp4（大小写不敏感），杜绝 ``a.mp4.mp4``；
    - 若去除后主名为空（如用户只填了 ``.mp4``），回退为 ``output``；
    - 直接追加单个 .mp4 后缀（保留文件名中间的点，如 ``a.b`` -> ``a.b.mp4``）。

    Args:
        name: 用户提供的文件名或完整路径。

    Returns:
        规范化后的文件名或完整路径（目录 + 单一的 .mp4 后缀）。
    """
    directory, base = os.path.split(name)
    # 1. 去掉末尾任意数量的 .mp4（大小写不敏感），杜绝 .mp4.mp4
    base_lower = base.lower()
    while base_lower.endswith(".mp4"):
        base = base[:-4]
        base_lower = base.lower()
    # 2. 兜底：主名为空时给默认名
    if not base:
        base = "output"
    # 2.5 清洗 Windows 非法文件名字符并限制长度（如网页标题自带的 '|' '?' 等）
    base = sanitize_filename_component(base)
    # 3. 追加单个 .mp4 后缀（保留文件名中间的点）
    base = base + ".mp4"
    return os.path.join(directory, base) if directory else base


def normalize_page_url(raw: str) -> str:
    """规范化用户输入的网页 URL：补 ``https://``、去首尾空白.

    仅做最轻量的修正，不验证可达性；保留原 query/锚点。

    Args:
        raw: 用户粘贴的网页地址（可能漏写协议，或带首尾空格）。

    Returns:
        规范化后的绝对 URL；空输入返回空字符串。
    """
    if not raw:
        return ""
    text = raw.strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text


def build_output_path(base_output: str, index: int, total: int) -> str:
    """多目标下载时生成带序号的输出文件名.

    规则（见 docs/system_design.md §3.4）：

    - ``total <= 1``：直接返回 ``normalize_mp4_filename(base_output)`` 原样，
      不追加序号；
    - ``total > 1``：主名后追加 ``_{index}``（index 为候选序号，便于对应列表），
      再经 ``normalize_mp4_filename`` 保证单一 ``.mp4`` 后缀。

    Args:
        base_output: 用户提供的输出文件路径（如 ``video.mp4``）。
        index: 候选序号（从 1 开始）。
        total: 本次要下载的目标总数。

    Returns:
        最终输出路径，如 ``video_1.mp4`` / ``video_2.mp4``。
    """
    if total <= 1:
        return normalize_mp4_filename(base_output)

    directory, base = os.path.split(base_output)
    # 去掉末尾任意数量的 .mp4（大小写不敏感），避免 video.mp4_1.mp4
    base_lower = base.lower()
    while base_lower.endswith(".mp4"):
        base = base[:-4]
        base_lower = base.lower()
    if not base:
        base = "output"
    base = sanitize_filename_component(base)
    base = f"{base}_{index}"
    base = base + ".mp4"
    return os.path.join(directory, base) if directory else base
