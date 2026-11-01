import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from m3u8_downloader import extractor
from m3u8_downloader.extraction_errors import LoginRequiredError

@pytest.fixture
def login_pages():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            status=401 if self.path=='/401' else 200
            if self.path=='/error':body=b'<title>Private player</title><script>setTimeout(()=>{throw new Error("please log in to watch")},100)</script>'
            elif self.path=='/overlay':body='<title>Private player</title><div class="player">请先登录后观看</div>'.encode()
            elif self.path=='/success':body=b'<script>setTimeout(()=>{throw new Error("please log in to watch")},100);fetch("/video.m3u8")</script>'
            elif self.path=='/video.m3u8':body=b'#EXTM3U\n#EXT-X-ENDLIST\n'
            else:body='<title>普通网页</title><nav>登录 / 注册</nav><p>没有播放器</p>'.encode()
            self.send_response(status);self.send_header('Content-Type','application/vnd.apple.mpegurl' if self.path.endswith('.m3u8') else 'text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join()

@pytest.mark.parametrize('worker',[False,True])
@pytest.mark.parametrize('path',['/error','/overlay'])
def test_explicit_player_login_gate_has_a_distinct_result(login_pages,worker,path):
    with pytest.raises(LoginRequiredError):
        extractor.extract_m3u8_from_page_with_title(login_pages+path,deep=True,estimate=False,no_proxy=True,stop_event=threading.Event() if worker else None)

@pytest.mark.parametrize('worker',[False,True])
def test_successful_candidates_take_precedence_over_login_messages(login_pages,worker):
    candidates,_=extractor.extract_m3u8_from_page_with_title(login_pages+'/success',deep=True,estimate=False,no_proxy=True,stop_event=threading.Event() if worker else None)
    assert [c.url for c in candidates]==[login_pages+'/video.m3u8']

def test_navigation_login_link_is_not_a_login_gate(login_pages):
    with pytest.raises(extractor.NoCandidateFoundError) as error:
        extractor.extract_m3u8_from_page_with_title(login_pages+'/ordinary',deep=True,estimate=False,no_proxy=True)
    assert not isinstance(error.value,LoginRequiredError)

def test_http_401_page_requests_login_without_browser(login_pages):
    with pytest.raises(LoginRequiredError):
        extractor.extract_m3u8_from_page_with_title(login_pages+'/401',deep=False,estimate=False,no_proxy=True)


def test_login_requirement_stops_retries_and_persists_for_user_input(tmp_path):
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest,TaskCoordinator
    from m3u8_downloader.gui_v2 import _task_status
    calls=[];waits=[]
    class Extractor:
        def extract(self,task,**kwargs):calls.append(task.id);raise LoginRequiredError()
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://example.test/watch',save_directory=str(tmp_path)))[0]
    result=TaskCoordinator(service,extractor=Extractor(),retry_wait=lambda *args:waits.append(args)).run_extraction(task.id)
    assert calls==[task.id] and waits==[]
    assert result.login_required and _task_status(result)=='需要登录'
    restored=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db')).get_task(task.id)
    assert restored.login_required


def test_login_prompt_is_once_per_site_and_cancel_keeps_task_pending(qtbot,tmp_path):
    from m3u8_downloader.gui_v2 import MainWindow
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest
    from m3u8_downloader.extraction_errors import LOGIN_REQUIRED_MESSAGE
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    tasks=service.create_tasks(CreateTaskRequest(addresses='https://example.test/one\nhttps://example.test/two',save_directory=str(tmp_path)))
    for task in tasks:service.fail_extraction(task.id,LOGIN_REQUIRED_MESSAGE)
    window=MainWindow(service);window._force_exit=True;qtbot.addWidget(window);window.show()
    window._prompt_for_login(tasks[0].id)
    first=window._login_prompt_box
    assert first is not None
    window._prompt_for_login(tasks[1].id)
    assert window._login_prompt_box is first
    next(button for button in first.buttons() if button.text()=='暂不填写').click()
    assert window._login_prompt_box is None
    window._prompt_for_login(tasks[0].id)
    assert window._login_prompt_box is None
    assert all(service.get_task(task.id).login_required for task in tasks)


def test_login_entry_saves_credentials_then_retries(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QDialog,QMenu
    from m3u8_downloader.gui_v2 import MainWindow,TaskSettingsDialog
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest,ExtractionStatus
    from m3u8_downloader.extraction_errors import LOGIN_REQUIRED_MESSAGE
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://example.test/watch',save_directory=str(tmp_path)))[0]
    service.fail_extraction(task.id,LOGIN_REQUIRED_MESSAGE)
    window=MainWindow(service);window._force_exit=True;qtbot.addWidget(window);window.show()
    class Dialog(TaskSettingsDialog):
        def exec(self):self.cookie.setText('session=synthetic');return QDialog.DialogCode.Accepted
    class Menu(QMenu):
        def exec(self,*args):next(action for action in self.actions() if action.text()=='填写登录信息').trigger()
    monkeypatch.setattr('m3u8_downloader.gui_v2.TaskSettingsDialog',Dialog)
    monkeypatch.setattr('m3u8_downloader.gui_v2.QMenu',Menu)
    window.task_list.customContextMenuRequested.emit(window.task_list.visualItemRect(window.task_list.item(0)).center())
    updated=service.get_task(task.id)
    assert updated.extraction_status is ExtractionStatus.WAITING and not updated.login_required
    assert updated.settings.protected_cookie.startswith('dpapi:')


def test_standalone_worker_reports_login_requirement(login_pages):
    import subprocess,sys
    result=subprocess.run([sys.executable,'-m','m3u8_downloader.deep_worker','--url',login_pages+'/overlay','--wait-ms','1200','--stream'],capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert result.returncode==5,result.stderr
    assert '需要登录' in result.stderr


def test_background_completion_opens_login_prompt_without_retry_storm(qtbot,tmp_path):
    from m3u8_downloader.background_v2 import TaskBackgroundController
    from m3u8_downloader.gui_v2 import MainWindow
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest,TaskCoordinator
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://example.test/private',save_directory=str(tmp_path)))[0]
    calls=[]
    class Extractor:
        def extract(self,*args,**kwargs):calls.append(1);raise LoginRequiredError()
    window=MainWindow(service);window._force_exit=True;qtbot.addWidget(window)
    controller=TaskBackgroundController(service,extraction_coordinator=TaskCoordinator(service,extractor=Extractor()),download_coordinator=object(),poll_interval_ms=10,parent=window)
    window.background_controller=controller
    controller.login_required.connect(window._prompt_for_login)
    controller.start()
    try:
        qtbot.waitUntil(lambda:window._login_prompt_box is not None,timeout=2000)
        qtbot.wait(80)
        assert calls==[1] and service.get_task(task.id).login_required
        window._login_prompt_box.reject()
    finally:controller.stop()

@pytest.mark.parametrize('message,expected',[
    ('请先登录',True),('登录后观看',True),('Please log in to watch',True),('Sign in to play',True),
    ('登录 / 注册',False),('未找到链接，可能需要登录',False),('HTTP 403',False),('无候选',False),
])
def test_only_explicit_login_phrases_are_used(message,expected):
    from m3u8_downloader.extraction_errors import LoginGate
    gate=LoginGate();gate.observe(message)
    assert gate.required is expected
