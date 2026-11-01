import json
import requests
import pytest
from m3u8_downloader.cookie_input import normalize_cookie_input,apply_cookie_input


def exported(url='https://media.example.test',domain='.example.test'):
    return json.dumps({'url':url,'cookies':[{'name':'session','value':'%2Bvalue%3D%24keep==','domain':domain,'path':'/','secure':True,'httpOnly':True,'hostOnly':False,'sameSite':'unspecified'}]})


def test_json_export_becomes_domain_scoped_http_and_browser_cookies():
    session=requests.Session()
    apply_cookie_input(session,exported(),'https://media.example.test/watch')
    assert session.prepare_request(requests.Request('GET','https://media.example.test/watch')).headers['Cookie']=='session=%2Bvalue%3D%24keep=='
    assert session.prepare_request(requests.Request('GET','https://api.example.test/api')).headers['Cookie']=='session=%2Bvalue%3D%24keep=='
    assert 'Cookie' not in session.prepare_request(requests.Request('GET','https://other.test/')).headers
    cookie=session._m3u8_browser_cookies[0]
    assert cookie['domain']=='.example.test' and cookie['httpOnly'] is True
    assert cookie['value']=='%2Bvalue%3D%24keep=='


@pytest.mark.parametrize('raw,expected',[(' session=hello==; n=2 ','session=hello==; n=2'),('Cookie: session=hello','session=hello'),('', '')])
def test_existing_cookie_header_format_remains_usable(raw,expected):
    assert normalize_cookie_input(raw)==expected


@pytest.mark.parametrize('raw',['{"cookies":', '{"cookies":{}}', '{"cookies":[{"name":"session"}]}', '{"cookies":[{"name":"bad name","value":"x"}]}', '{"cookies":[{"name":"session","value":"secret\\r\\nInjected: yes"}]}'])
def test_invalid_export_is_rejected_without_echoing_credentials(raw):
    with pytest.raises(ValueError) as error:normalize_cookie_input(raw)
    assert 'secret' not in str(error.value) and raw not in str(error.value)


def test_cookie_array_and_host_only_path_are_preserved():
    data=[{'name':'session','value':'value','domain':'media.example.test','hostOnly':True,'path':'/watch','secure':True}]
    session=requests.Session();apply_cookie_input(session,json.dumps(data),'https://media.example.test/watch/1')
    assert 'Cookie' in session.prepare_request(requests.Request('GET','https://media.example.test/watch/1')).headers
    assert 'Cookie' not in session.prepare_request(requests.Request('GET','https://media.example.test/other')).headers
    assert session._m3u8_browser_cookies[0]['domain']=='media.example.test'


def test_unrelated_export_domain_is_not_imported():
    session=requests.Session();apply_cookie_input(session,exported(),'https://unrelated.test/watch')
    assert not list(session.cookies)
    assert session._m3u8_browser_cookies==[]


def test_task_settings_accepts_json_and_keeps_it_encrypted(qtbot,tmp_path):
    from m3u8_downloader.gui_v2 import TaskSettingsDialog
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest
    from m3u8_downloader.secrets_v2 import unprotect_secret
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://media.example.test/watch',save_directory=str(tmp_path)))[0]
    dialog=TaskSettingsDialog(task);qtbot.addWidget(dialog)
    dialog.cookie.setText(exported())
    dialog.accept()
    settings=dialog.settings()
    assert settings.protected_cookie.startswith('dpapi:')
    assert '%2Bvalue' not in settings.protected_cookie
    assert json.loads(unprotect_secret(settings.protected_cookie))==json.loads(exported())


def test_task_settings_invalid_json_does_not_close_or_replace_login(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox,QDialog
    from m3u8_downloader.gui_v2 import TaskSettingsDialog
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://media.example.test/watch',save_directory=str(tmp_path)))[0]
    dialog=TaskSettingsDialog(task);qtbot.addWidget(dialog)
    messages=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda _parent,_title,message:messages.append(message))
    dialog.cookie.setText('{"cookies":')
    dialog.accept()
    assert dialog.result()!=QDialog.DialogCode.Accepted
    assert messages and service.get_task(task.id).settings.protected_cookie==''

@pytest.mark.parametrize('adapter',['extract','download'])
def test_saved_encrypted_json_is_understood_by_task_adapters(tmp_path,adapter):
    from pathlib import Path
    from m3u8_downloader.secrets_v2 import protect_secret
    from m3u8_downloader.tasking import TaskService,SQLiteTaskRepository,CreateTaskRequest,TaskSettings
    from m3u8_downloader.extractor_adapter_v2 import ExistingExtractorAdapter
    from m3u8_downloader.downloader_adapter_v2 import ExistingDownloaderAdapter
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://media.example.test/video.m3u8',save_directory=str(tmp_path),settings=TaskSettings(protected_cookie=protect_secret(exported()))))[0]
    captured=[]
    def check(session):
        captured.append(session.prepare_request(requests.Request('GET',task.source_url)).headers.get('Cookie'))
        assert 'Cookie' not in session.prepare_request(requests.Request('GET','https://unrelated.test/')).headers
    if adapter=='extract':
        def extract(url,**kwargs):
            check(kwargs['session']);return [],''
        ExistingExtractorAdapter(extract_function=extract).extract(task,deep=True,on_candidate=lambda _:None,on_title=lambda _:None,stop_event=None)
    else:
        class Downloader:
            def __init__(self,**kwargs):self._session=requests.Session()
            def download(self):check(self._session);return Path(tmp_path/'test.mp4')
        ExistingDownloaderAdapter(downloader_factory=Downloader).download(task,service.list_items(task.id)[0],tmp_path/'test.mp4',stop_event=None,on_progress=lambda _:None,on_log=lambda _:None)
    assert captured==['session=%2Bvalue%3D%24keep==']
