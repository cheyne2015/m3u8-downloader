from dataclasses import replace

from m3u8_downloader.tasking import (
    CreateTaskRequest, SQLiteTaskRepository, TaskService, TaskSettings,
)


def test_saved_login_is_reused_by_existing_and_new_same_host_tasks(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    first, existing, other = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/one\nhttps://video.example/two\nhttps://other.example/one",
        save_directory=str(tmp_path),
    ))
    service.update_task_settings(first.id, replace(first.settings, protected_cookie="encrypted-login"))
    # Reopen through the public service: sharing must persist across processes.
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    assert service.start_extraction(existing.id).settings.protected_cookie == "encrypted-login"
    assert not service.start_extraction(other.id).settings.protected_cookie
    new = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/three", save_directory=str(tmp_path),
    ))[0]
    assert new.settings.protected_cookie == "encrypted-login"


def test_task_specific_login_wins_and_other_hosts_do_not_inherit(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    service.save_site_profile("https://video.example", TaskSettings(protected_cookie="shared"))
    explicit = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/one", save_directory=str(tmp_path),
        settings=TaskSettings(protected_cookie="own-login"),
    ))[0]
    subdomain = service.create_tasks(CreateTaskRequest(
        addresses="https://sub.video.example/one", save_directory=str(tmp_path),
    ))[0]
    assert service.start_extraction(explicit.id).settings.protected_cookie == "own-login"
    assert not service.start_extraction(subdomain.id).settings.protected_cookie


def test_late_success_does_not_replace_new_login_or_site_parameters(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    service.save_site_profile("https://video.example", TaskSettings(
        protected_cookie="old", timeout_seconds=88, preferred_extraction_mode="deep",
    ), preferred_mode="deep")
    old, edited = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/old\nhttps://video.example/edited",
        save_directory=str(tmp_path),
    ))
    service.update_task_settings(edited.id, replace(edited.settings, protected_cookie="new"))
    profile = service.list_site_profiles()[0]
    assert profile.timeout_seconds == 88 and profile.preferred_extraction_mode == "deep"
    service.record_site_extraction(old.id, success=True, effective_mode="deep",
                                   elapsed_seconds=1, candidate_count=1)
    assert service.list_site_profiles()[0].protected_cookie == "new"


def test_login_saved_by_previous_version_can_be_reused_without_reentry(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository)
    old, waiting = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/old\nhttps://video.example/waiting",
        save_directory=str(tmp_path),
    ))
    repository.save_many([replace(old, settings=replace(old.settings, protected_cookie="legacy"))])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    assert service.start_extraction(waiting.id).settings.protected_cookie == "legacy"
    new = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/new", save_directory=str(tmp_path),
    ))[0]
    assert new.settings.protected_cookie == "legacy"


def test_reused_login_reaches_the_real_page_request(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from m3u8_downloader.extractor_adapter_v2 import ExistingExtractorAdapter
    from m3u8_downloader.secrets_v2 import protect_secret

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            accepted = self.headers.get("Cookie") == "session=allowed"
            if self.path.endswith(".m3u8"):
                body = b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n#EXT-X-ENDLIST\n"
            else:
                body = b'<video src="/video.m3u8"></video>'
            self.send_response(200 if accepted else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
        source, target = service.create_tasks(CreateTaskRequest(
            addresses=base + "/one\n" + base + "/two", save_directory=str(tmp_path),
        ))
        service.update_task_settings(source.id, replace(
            source.settings, protected_cookie=protect_secret("session=allowed"),
        ))
        task = service.start_extraction(target.id)
        candidates = ExistingExtractorAdapter().extract(
            task, deep=False, on_candidate=lambda item: None,
            on_title=lambda title: None, stop_event=threading.Event(),
        )
        assert [item.url for item in candidates] == [base + "/video.m3u8"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
