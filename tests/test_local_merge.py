from pathlib import Path
import pytest


def test_encrypted_local_playlist_merges_without_changing_sources(tmp_path):
    import hashlib
    import subprocess
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from m3u8_downloader.ffmpeg_v2 import resolve_ffmpeg_executable
    from m3u8_downloader.local_merge import merge_local_playlist
    from m3u8_downloader.merger import validate_media_file
    raw = tmp_path / 'raw.ts'
    subprocess.run([resolve_ffmpeg_executable(), '-v', 'error', '-f', 'lavfi',
        '-i', 'color=c=blue:s=64x64:r=25', '-t', '1', '-c:v', 'libx264',
        '-f', 'mpegts', str(raw)], check=True, capture_output=True)
    key = bytes(range(16))
    iv = bytes(16)
    (tmp_path / '0.key').write_bytes(key)
    (tmp_path / '0.ts').write_bytes(AES.new(key, AES.MODE_CBC, iv).encrypt(pad(raw.read_bytes(), 16)))
    playlist = tmp_path / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXT-X-TARGETDURATION:1\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="0.key",IV=0x' + iv.hex() +
        '\n#EXTINF:1,\n0.ts\n#EXT-X-ENDLIST\n')
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    output = tmp_path / 'result.mp4'
    assert merge_local_playlist(playlist, output) == output
    validate_media_file(str(output))
    assert all(hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
               for name, digest in before.items())
    with pytest.raises(FileExistsError):
        merge_local_playlist(playlist, output)
    assert not list(tmp_path.glob('.*.merge-*.mp4'))


def test_local_merge_cancel_does_not_create_output(tmp_path):
    import threading
    from m3u8_downloader.downloader import DownloadCancelled
    from m3u8_downloader.local_merge import merge_local_playlist
    (tmp_path / '0.ts').write_bytes(b'local')
    playlist = tmp_path / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXTINF:1,\n0.ts\n#EXT-X-ENDLIST\n')
    stop = threading.Event()
    stop.set()
    with pytest.raises(DownloadCancelled):
        merge_local_playlist(playlist, tmp_path / 'out.mp4', stop_event=stop)
    assert not (tmp_path / 'out.mp4').exists()


def test_local_merge_preview_follows_playlist_order_and_checks_files(tmp_path):
    from m3u8_downloader.local_merge import inspect_local_playlist
    (tmp_path / '10.ts').write_bytes(b'a')
    (tmp_path / '2.ts').write_bytes(b'b')
    playlist = tmp_path / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXTINF:2,\n10.ts\n#EXTINF:3,\n2.ts\n#EXT-X-ENDLIST\n')
    preview = inspect_local_playlist(playlist)
    assert [p.name for p in preview.segments] == ['10.ts', '2.ts']
    assert preview.duration_seconds == 5
    (tmp_path / '2.ts').unlink()
    with pytest.raises(ValueError, match='缺少'):
        inspect_local_playlist(playlist)


def test_local_merge_rejects_remote_references(tmp_path):
    from m3u8_downloader.local_merge import inspect_local_playlist
    playlist = tmp_path / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXTINF:2,\nhttps://example.test/a.ts\n#EXT-X-ENDLIST\n')
    with pytest.raises(ValueError, match='本地'):
        inspect_local_playlist(playlist)


def test_cancel_stops_real_ffmpeg_process(monkeypatch):
    import subprocess
    import threading
    from m3u8_downloader import local_merge
    from m3u8_downloader.downloader import DownloadCancelled
    real_popen = subprocess.Popen
    children = []

    def record(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(local_merge.subprocess, 'Popen', record)
    stop = threading.Event()
    timer = threading.Timer(.5, stop.set)
    timer.start()
    try:
        with pytest.raises(DownloadCancelled):
            local_merge._run_ffmpeg(['-re', '-f', 'lavfi', '-i',
                'color=c=blue:s=64x64:r=25', '-f', 'null', '-'], stop)
        assert len(children) == 1
        assert children[0].poll() is not None
    finally:
        timer.cancel()
