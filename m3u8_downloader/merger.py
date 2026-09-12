"""TS 片段合并与 MP4 转换模块.

优先使用 ffmpeg 合并，ffmpeg 不可用时使用 Python 直接合并。
支持 AES-128 解密。
"""

import os
import subprocess
import shutil
from typing import List, Optional

from m3u8_downloader.parser import M3U8Segment
from m3u8_downloader.utils import is_ffmpeg_available
from m3u8_downloader.ffmpeg_v2 import resolve_ffmpeg_executable


def _decrypt_segment(
    data: bytes,
    key: bytes,
    iv: Optional[bytes] = None,
) -> bytes:
    """使用 AES-128-CBC 解密 TS 片段.

    Args:
        data: 加密的 TS 片段数据.
        key: AES 密钥.
        iv: AES IV，如果为 None 则使用默认 IV（segment sequence number）.

    Returns:
        解密后的数据.
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    if iv is None:
        iv = b"\x00" * 16

    cipher = AES.new(key, AES.MODE_CBC, iv)
    raw = cipher.decrypt(data)
    try:
        decrypted = unpad(raw, AES.block_size)
    except ValueError:
        # 如果 unpad 失败，直接返回解密结果（某些流不使用标准 padding）
        decrypted = raw
    return decrypted


def decrypt_and_save_segment(
    input_path: str,
    output_path: str,
    key: bytes,
    iv: Optional[bytes] = None,
) -> None:
    """读取加密的 TS 片段，解密后保存.

    Args:
        input_path: 加密的 TS 片段文件路径.
        output_path: 解密后的 TS 片段文件路径.
        key: AES 密钥.
        iv: AES IV.
    """
    with open(input_path, "rb") as f:
        encrypted_data = f.read()

    decrypted_data = _decrypt_segment(encrypted_data, key, iv)

    with open(output_path, "wb") as f:
        f.write(decrypted_data)


def merge_ts_files_binary(
    segment_paths: List[str],
    output_path: str,
) -> None:
    """使用 Python 二进制拼接方式合并 TS 片段.

    Args:
        segment_paths: TS 片段文件路径列表（按顺序）.
        output_path: 输出文件路径.
    """
    with open(output_path, "wb") as out_f:
        for seg_path in segment_paths:
            if not os.path.exists(seg_path):
                raise FileNotFoundError(f"TS 片段文件不存在: {seg_path}")
            with open(seg_path, "rb") as in_f:
                shutil.copyfileobj(in_f, out_f)


def merge_with_ffmpeg_concat(
    segment_paths: List[str],
    output_path: str,
) -> None:
    """使用 ffmpeg concat 协议合并 TS 片段.

    生成 ffmpeg concat 格式的文件列表，然后调用 ffmpeg 合并。

    Args:
        segment_paths: TS 片段文件路径列表（按顺序）.
        output_path: 输出文件路径.

    Raises:
        RuntimeError: 如果 ffmpeg 执行失败.
    """
    # 创建 concat 文件列表
    concat_dir = os.path.dirname(output_path)
    concat_list_path = os.path.join(concat_dir, "_concat_list.txt")

    try:
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg_path in segment_paths:
                # ffmpeg concat 格式需要对单引号转义
                safe_path = seg_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # 调用 ffmpeg
        cmd = [
            resolve_ffmpeg_executable(),
            "-y",  # 覆盖输出
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            output_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 合并失败 (返回码 {result.returncode}):\n{result.stderr}"
            )
    finally:
        # 清理临时 concat 列表文件
        if os.path.exists(concat_list_path):
            os.remove(concat_list_path)


def convert_ts_to_mp4_ffmpeg(
    ts_path: str,
    mp4_path: str,
) -> None:
    """使用 ffmpeg 将 TS 文件转码为 MP4.

    Args:
        ts_path: 输入的 TS 文件路径.
        mp4_path: 输出的 MP4 文件路径.

    Raises:
        RuntimeError: 如果 ffmpeg 执行失败.
    """
    cmd = [
        resolve_ffmpeg_executable(),
        "-y",
        "-i", ts_path,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        mp4_path,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 转码失败 (返回码 {result.returncode}):\n{result.stderr}"
        )


def validate_media_file(path: str) -> None:
    """快速验证容器、音视频流和开头一秒能否被正常读取。"""
    result = subprocess.run(
        [
            resolve_ffmpeg_executable(),
            "-v", "error",
            "-xerror",
            "-i", path,
            "-t", "1",
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-f", "null",
            os.devnull,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = (result.stderr or "无法识别有效的音视频流").strip()
        raise RuntimeError(f"媒体文件校验失败：{detail}")


def merge_segments_to_mp4(
    segment_paths: List[str],
    output_path: str,
    use_ffmpeg: bool = True,
) -> str:
    """将 TS 片段合并为 MP4 文件.

    MP4 输出必须经过 ffmpeg 成功封装。转换失败或 ffmpeg 不可用时抛出错误，
    绝不把未经验证的片段拼接数据改名伪装成 MP4。

    Args:
        segment_paths: TS 片段文件路径列表（按顺序）.
        output_path: 目标 MP4 输出路径.
        use_ffmpeg: 是否尝试使用 ffmpeg.

    Returns:
        最终输出文件的路径.
    """
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # 先合并为临时 TS 文件
    temp_ts_path = output_path.rsplit(".", 1)[0] + "_temp.ts"
    if output_path.endswith(".ts"):
        temp_ts_path = output_path + "_temp.ts"

    print("正在合并 TS 片段...")
    merge_ts_files_binary(segment_paths, temp_ts_path)

    ffmpeg_available = use_ffmpeg and is_ffmpeg_available()

    if output_path.endswith(".mp4"):
        if not ffmpeg_available:
            raise RuntimeError("无法生成 MP4：ffmpeg 不可用或已被关闭")
        output_existed = os.path.exists(output_path)
        try:
            print("正在使用 ffmpeg 转码为 MP4...")
            convert_ts_to_mp4_ffmpeg(temp_ts_path, output_path)
            validate_media_file(output_path)
            # 转码成功，删除临时 TS 文件
            if os.path.exists(temp_ts_path):
                os.remove(temp_ts_path)
            print(f"ffmpeg 转码完成: {output_path}")
            return output_path
        except RuntimeError as e:
            print(f"ffmpeg 转码失败: {e}")
            if not output_existed and os.path.exists(output_path):
                os.remove(output_path)
            raise

    if temp_ts_path != output_path:
        shutil.move(temp_ts_path, output_path)
    return output_path


def decrypt_segments(
    segments: List[M3U8Segment],
    segment_paths: List[str],
) -> List[str]:
    """解密加密的 TS 片段.

    如果片段未加密，直接返回原路径。如果片段已加密，解密后保存到新路径。

    Args:
        segments: M3U8Segment 列表（包含加密信息）.
        segment_paths: 对应的文件路径列表.

    Returns:
        解密后的文件路径列表.
    """
    result_paths: List[str] = []

    for segment, seg_path in zip(segments, segment_paths):
        if segment.key is None or segment.key.method == "NONE":
            result_paths.append(seg_path)
            continue

        if segment.key.key is None:
            raise RuntimeError(f"片段 {seg_path} 的解密密钥未获取")

        # 解密后的文件路径
        decrypted_path = seg_path + ".dec"
        try:
            decrypt_and_save_segment(
                input_path=seg_path,
                output_path=decrypted_path,
                key=segment.key.key,
                # RFC 8216: 未显式提供 IV 时，用 media sequence number 的
                # 128 位大端表示；不能对每个片段都使用全零 IV。
                iv=(segment.key.iv if segment.key.iv is not None
                    else segment.sequence.to_bytes(16, "big")),
            )
            result_paths.append(decrypted_path)
        except Exception as e:
            try:
                if os.path.exists(decrypted_path):
                    os.remove(decrypted_path)
            except OSError:
                pass
            raise RuntimeError(f"片段 {seg_path} 解密失败: {e}") from e

    return result_paths
