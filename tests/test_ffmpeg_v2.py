"""随包 ffmpeg 解析测试。"""

from m3u8_downloader.ffmpeg_v2 import resolve_ffmpeg_executable


def test_one_folder_build_prefers_internal_bundled_ffmpeg(tmp_path):
    executable = tmp_path / "m3u8-dl-v2.exe"
    internal = tmp_path / "_internal"
    internal.mkdir()
    bundled = internal / "ffmpeg.exe"
    bundled.touch()

    resolved = resolve_ffmpeg_executable(executable, which=lambda _name: "system-ffmpeg")

    assert resolved == str(bundled)

