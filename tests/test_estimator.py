"""媒体信息估算的批处理策略测试。"""

import requests

from m3u8_downloader import estimator


def test_estimate_many_forwards_requested_sample_count(monkeypatch):
    captured = []

    def fake_estimate(url, session, timeout, head_samples):
        captured.append((url, head_samples))
        return estimator.SizeEstimate()

    monkeypatch.setattr(estimator, "_safe_estimate", fake_estimate)
    with requests.Session() as session:
        estimator.estimate_many(
            ["https://cdn.example/a.m3u8"],
            session=session,
            max_workers=1,
            head_samples=1,
        )

    assert captured == [("https://cdn.example/a.m3u8", 1)]
