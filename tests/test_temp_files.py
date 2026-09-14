from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

from m3u8_downloader.temp_files import TempFileManager


def _job(root: Path, fingerprint: str, output: Path, size: int) -> Path:
    suffix = sha256(str(output.resolve()).lower().encode("utf-8")).hexdigest()[:8]
    job = root / ".tmp" / f"job-{fingerprint}-{suffix}"
    job.mkdir(parents=True)
    (job / "seg_00000.ts.part").write_bytes(b"x" * size)
    return job


def test_scan_and_cleanup_preserve_resumable_cache_for_unfinished_items(tmp_path):
    active_output = tmp_path / "active.mp4"
    stale_output = tmp_path / "old.mp4"
    protected = _job(tmp_path, "a" * 16, active_output, 11)
    cleanable = _job(tmp_path, "b" * 16, stale_output, 17)
    task = SimpleNamespace(save_directory=str(tmp_path))
    item = SimpleNamespace(output_path=str(active_output), status=SimpleNamespace(value="paused"))

    manager = TempFileManager()
    scan = manager.scan([task], {id(task): [item]})

    assert scan.total_bytes == 28
    assert scan.cleanable_bytes == 17
    assert scan.protected_bytes == 11
    assert scan.cleanable_jobs == (cleanable,)

    result = manager.cleanup(scan)
    assert result.removed_bytes == 17
    assert result.removed_jobs == 1
    assert protected.is_dir()
    assert not cleanable.exists()


def test_scan_finds_orphan_job_in_one_level_task_folder(tmp_path):
    folder = tmp_path / "网页标题"
    orphan = _job(folder, "c" * 16, folder / "old.mp4", 23)
    task = SimpleNamespace(save_directory=str(tmp_path))

    scan = TempFileManager().scan([task], {id(task): []})

    assert scan.cleanable_jobs == (orphan,)
    assert scan.cleanable_bytes == 23


def test_cleanup_never_removes_a_job_that_became_active_after_scan(tmp_path):
    output = tmp_path / "movie.mp4"
    job = _job(tmp_path, "d" * 16, output, 31)
    task = SimpleNamespace(save_directory=str(tmp_path))
    manager = TempFileManager(
        active_job_paths=lambda: {os.path.normcase(os.path.abspath(job))},
    )
    scan = manager.scan([task], {id(task): []})

    result = manager.cleanup(scan)

    assert result.removed_jobs == 0
    assert result.failed_jobs == 0
    assert job.is_dir()
