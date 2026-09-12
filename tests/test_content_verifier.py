"""疑似重复内容的远程小样本验证。"""

from types import SimpleNamespace

from m3u8_downloader.content_verifier import ContentDuplicateVerifier
from m3u8_downloader.tasking import TaskSettings


class FakeResponse:
    def __init__(self, content):
        self.content = content if isinstance(content, bytes) else content.encode()
        self.text = self.content.decode(errors="replace")

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.headers = {}
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(self.responses[url])

    def close(self):
        return None


def test_high_suspicion_content_comparison_samples_start_middle_and_end_only():
    playlist_a = """#EXTM3U
#EXTINF:10,
a0.ts
#EXTINF:10,
a1.ts
#EXTINF:10,
a2.ts
#EXTINF:10,
a3.ts
#EXTINF:10,
a4.ts
"""
    playlist_b = playlist_a.replace("a", "b")
    responses = {
        "https://a.example/video.m3u8": playlist_a,
        "https://b.example/video.m3u8": playlist_b,
        "https://a.example/a0.ts": b"same-start",
        "https://a.example/a2.ts": b"same-middle",
        "https://a.example/a4.ts": b"same-end",
        "https://b.example/b0.ts": b"same-start",
        "https://b.example/b2.ts": b"same-middle",
        "https://b.example/b4.ts": b"same-end",
    }
    sessions = []

    def session_factory(*_args, **_kwargs):
        session = FakeSession(responses)
        sessions.append(session)
        return session

    verifier = ContentDuplicateVerifier(session_factory=session_factory)
    task = SimpleNamespace(settings=TaskSettings())
    first = [SimpleNamespace(
        valid=True, duration_seconds=50, segment_count=5, estimated_bytes=1000,
        source_url="https://a.example/video.m3u8",
    )]
    second = [SimpleNamespace(
        valid=True, duration_seconds=50, segment_count=5, estimated_bytes=1000,
        source_url="https://b.example/video.m3u8",
    )]

    assert verifier.matches(task, first, task, second) is True
    sampled = [
        url for session in sessions for url, options in session.requests
        if options.get("stream")
    ]
    assert sampled == [
        "https://a.example/a0.ts",
        "https://a.example/a2.ts",
        "https://a.example/a4.ts",
        "https://b.example/b0.ts",
        "https://b.example/b2.ts",
        "https://b.example/b4.ts",
    ]
