"""正式冻结包配置的关键回归测试。"""

from pathlib import Path


def test_build_filters_incompatible_icu_dlls_discovered_from_path():
    spec = (Path(__file__).parents[1] / "build-v2.spec").read_text(encoding="utf-8")

    assert "icuuc.dll" in spec
    assert "icudt78.dll" in spec
    assert "a.binaries" in spec


def test_build_uses_web_extraction_icon_and_bundles_icon_assets():
    spec = (Path(__file__).parents[1] / "build-v2.spec").read_text(encoding="utf-8")

    assert 'icon="m3u8_downloader/assets/m3u8-downloader.ico"' in spec
    assert '("m3u8_downloader/assets", "m3u8_downloader/assets")' in spec


def test_deep_runtime_build_is_self_contained_and_writes_compatibility_manifest():
    spec = (Path(__file__).parents[1] / "build-deep-runtime.spec").read_text(
        encoding="utf-8"
    )

    assert 'collect_all("playwright")' in spec
    assert "chromium_headless_shell-" in spec
    assert 'name="deep-worker"' in spec
    assert 'name="deep-runtime"' in spec
    assert 'contents_directory="."' in spec
    assert 'prefix=f"browsers/{headless.name}"' in spec
    assert '"protocol_version": 1' in spec
    assert '"runtime_version": "1.0.0"' in spec
    assert '"runtime.json"' in spec
