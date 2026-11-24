from m3u8_downloader.douyin import find_video_record, playback_urls, video_id


def test_douyin_links_identify_only_explicit_target():
    target = "7692632885056097574"
    assert video_id(f"https://www.douyin.com/jingxuan?modal_id={target}") == target
    assert video_id(f"https://www.douyin.com/video/{target}") == target
    assert video_id(f"https://example.net/jingxuan?modal_id={target}") is None
    assert video_id(f"https://www.douyin.com/jingxuan?modal_id={target}&modal_id=1") is None


def test_feed_preload_is_never_selected_in_place_of_target():
    target = {"aweme_id": "42", "video": {"duration": 2662145}}
    preload = {"aweme_id": "43", "video": {"duration": 39851}}
    assert find_video_record({"items": [target, preload]}, "42") is target
    assert find_video_record({"items": [preload]}, "42") is None


def test_playback_candidates_keep_signed_extensionless_urls():
    url = "https://v11-weba.douyinvod.com/opaque/?signature=example"
    record = {"video": {"play_addr": {"url_list": [
        url, url, "https://v11-weba.douyinvod.com.example.net/other/",
    ]}}}
    assert playback_urls(record) == [url]


def test_mp4_download_publishes_valid_media(tmp_path):
    import functools
    import http.server
    import subprocess
    import threading

    from m3u8_downloader.downloader import M3U8Downloader
    from m3u8_downloader.ffmpeg_v2 import resolve_ffmpeg_executable

    source = tmp_path / "source.mp4"
    subprocess.run([
        resolve_ffmpeg_executable(), "-v", "error", "-f", "lavfi", "-i",
        "color=s=64x64:r=10", "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            output = tmp_path / "downloaded.mp4"
            url = f"http://127.0.0.1:{server.server_port}/source.mp4"
            M3U8Downloader(url, output=str(output), max_retries=0).download()
            assert output.read_bytes() == source.read_bytes()
        finally:
            server.shutdown()
            worker.join()
