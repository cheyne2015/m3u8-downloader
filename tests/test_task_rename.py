from pathlib import Path
from unittest.mock import patch
import pytest
from m3u8_downloader.tasking import TaskService, SQLiteTaskRepository, CreateTaskRequest, Candidate, OutputPlanner


def completed_task(tmp_path, count):
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://example.test/watch',save_directory=str(tmp_path/'output')))[0]
    service.apply_page_title(task.id,'网页标题')
    service.add_candidates(task.id,[Candidate(f'https://example.test/{i}.m3u8') for i in range(count)])
    service.finish_extraction(task.id)
    items=service.prepare_output_paths(task.id,OutputPlanner())
    for i,item in enumerate(items):
        path=Path(item.output_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(f'video-{i}'.encode())
        service.complete_item(task.id,item.id,path)
    return service,task


@pytest.mark.parametrize('count',[1,2])
def test_task_rename_updates_existing_disk_files_and_persisted_paths(tmp_path,count):
    service,task=completed_task(tmp_path,count)
    old=[Path(item.output_path) for item in service.list_items(task.id)]
    service.rename_task(task.id,'新名称')
    restored=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    assert restored.get_task(task.id).name=='新名称'
    for i,item in enumerate(restored.list_items(task.id)):
        expected=old[i].with_name('新名称.mp4' if count==1 else f'新名称_{i+1:02d}.mp4')
        assert Path(item.output_path)==expected
        assert expected.read_bytes()==f'video-{i}'.encode()
        assert not old[i].exists()


def test_task_rename_does_not_overwrite_an_existing_file(tmp_path):
    service,task=completed_task(tmp_path,1)
    old=Path(service.list_items(task.id)[0].output_path)
    other=old.with_name('新名称.mp4');other.write_bytes(b'unrelated')
    service.rename_task(task.id,'新名称')
    renamed=Path(service.list_items(task.id)[0].output_path)
    assert renamed.name=='新名称 (1).mp4'
    assert other.read_bytes()==b'unrelated'
    assert renamed.read_bytes()==b'video-0'


def test_task_rename_rolls_back_files_and_task_name_on_move_failure(tmp_path):
    service,task=completed_task(tmp_path,2)
    before=service.list_items(task.id)
    before_name=service.get_task(task.id).name
    original=Path.rename
    calls=0
    def fail_one(source,target):
        nonlocal calls
        calls+=1
        if calls==4: raise PermissionError('file busy')
        return original(source,target)
    with patch.object(Path,'rename',fail_one):
        with pytest.raises(PermissionError):service.rename_task(task.id,'新名称')
    assert service.get_task(task.id).name==before_name
    assert service.list_items(task.id)==before
    assert [Path(item.output_path).read_bytes() for item in before]==[b'video-0',b'video-1']


def test_running_output_keeps_its_path_until_completion(tmp_path):
    service,task=completed_task(tmp_path,1)
    item=service.list_items(task.id)[0]
    old=Path(item.output_path)
    service.start_item(task.id,item.id)
    service.rename_task(task.id,'下载中新名')
    assert service.get_item(task.id,item.id).output_path==str(old)
    assert old.exists()
    result=service.complete_item(task.id,item.id,old)
    assert Path(result.output_path).name=='下载中新名.mp4'
    assert Path(result.output_path).read_bytes()==b'video-0'


def test_renaming_a_paused_download_keeps_its_segment_cache_identity(tmp_path):
    import hashlib,os
    service,task=completed_task(tmp_path,1)
    item=service.list_items(task.id)[0]
    old=Path(item.output_path);old.unlink()
    fingerprint=hashlib.sha256(os.path.abspath(old).lower().encode('utf-8')).hexdigest()[:8]
    cache=old.parent/'.tmp'/f'job-1234567890abcdef-{fingerprint}'
    cache.mkdir(parents=True);(cache/'seg_00000.ts').write_bytes(b'cached')
    service.start_item(task.id,item.id)
    service.pause_item(task.id,item.id)
    service.rename_task(task.id,'恢复下载')
    service.resume_item(task.id,item.id)
    planned=service.prepare_output_paths(task.id,OutputPlanner())
    assert Path(planned[0].output_path)==old
    assert (cache/'seg_00000.ts').read_bytes()==b'cached'


def test_missing_file_is_not_falsely_relinked_when_renaming_task(tmp_path):
    service,task=completed_task(tmp_path,1)
    item=service.list_items(task.id)[0]
    Path(item.output_path).unlink()
    service.rename_task(task.id,'新名称')
    assert service.get_item(task.id,item.id).output_path==item.output_path
    assert service.get_task(task.id).name=='新名称'


def test_parent_download_finishes_using_name_changed_during_download(tmp_path):
    from m3u8_downloader.tasking import DownloadCoordinator,DownloadStatus
    service=TaskService(SQLiteTaskRepository(tmp_path/'tasks.db'))
    task=service.create_tasks(CreateTaskRequest(addresses='https://example.test/video.m3u8',save_directory=str(tmp_path/'output')))[0]
    class Downloader:
        def download(self,task,item,output,**kwargs):
            service.rename_task(task.id,'下载途中修改')
            output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(b'video')
            return output
    result=DownloadCoordinator(service,downloader=Downloader()).run_parent(task.id)
    assert result.download_status is DownloadStatus.COMPLETED
    item=service.list_items(task.id)[0]
    assert Path(item.output_path).name=='下载途中修改.mp4'
    assert Path(item.output_path).read_bytes()==b'video'


def test_task_rename_applies_case_only_changes_on_disk(tmp_path):
    service,task=completed_task(tmp_path,1)
    service.rename_task(task.id,'video')
    service.rename_task(task.id,'VIDEO')
    item=service.list_items(task.id)[0]
    names=[path.name for path in Path(item.output_path).parent.iterdir() if path.is_file()]
    assert 'VIDEO.mp4' in names and 'video.mp4' not in names


def test_parent_context_menu_rename_updates_disk_file(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QInputDialog,QMenu
    from m3u8_downloader.gui_v2 import MainWindow
    from m3u8_downloader.background_v2 import TaskBackgroundController
    service,task=completed_task(tmp_path,1)
    service.finish_parent_if_handled(task.id)
    window=MainWindow(service);window._force_exit=True;qtbot.addWidget(window)
    window.background_controller=TaskBackgroundController(service,extraction_coordinator=object(),download_coordinator=object(),parent=window)
    window.show();window._switch_view(1);window.completed_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog,'getText',lambda *args,**kwargs:('菜单新名',True))
    class Menu(QMenu):
        def exec(self,*args):
            next(action for action in self.actions() if action.text()=='重命名任务').trigger()
    monkeypatch.setattr('m3u8_downloader.gui_v2.QMenu',Menu)
    window.completed_list.customContextMenuRequested.emit(window.completed_list.visualItemRect(window.completed_list.item(0)).center())
    assert service.get_task(task.id).name=='菜单新名'
    item=service.list_items(task.id)[0]
    assert Path(item.output_path).name=='菜单新名.mp4'
    assert Path(item.output_path).read_bytes()==b'video-0'
