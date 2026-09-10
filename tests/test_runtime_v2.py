"""新版运行目录规则测试。"""

from m3u8_downloader.runtime_v2 import resolve_data_directory


def test_default_data_directory_uses_local_app_data(tmp_path):
    executable = tmp_path / "program" / "m3u8-dl.exe"
    local = tmp_path / "local"
    assert resolve_data_directory(executable, local) == local / "m3u8-downloader"


def test_portable_flag_keeps_new_database_beside_program(tmp_path):
    program = tmp_path / "program"
    program.mkdir()
    executable = program / "m3u8-dl.exe"
    (program / "portable.flag").touch()
    assert resolve_data_directory(executable, tmp_path / "local") == program / "data"

