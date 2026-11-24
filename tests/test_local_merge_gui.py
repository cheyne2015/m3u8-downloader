from m3u8_downloader.local_merge_gui import LocalMergeDialog


def test_dialog_background_merge_cancel_and_finish(qtbot, tmp_path):
    calls = []
    dialog = LocalMergeDialog(None, lambda operation, callback: calls.append((operation, callback)))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.playlist_edit.setText(str(tmp_path / 'index.m3u8'))
    dialog.output_edit.setText(str(tmp_path / 'result.mp4'))
    dialog.start_button.click()
    assert len(calls) == 1
    assert not dialog.start_button.isEnabled()
    dialog.cancel_button.click()
    assert dialog._stop.is_set()
    assert dialog.isVisible()
    calls[0][1](None, '已取消本地合并')
    assert dialog.start_button.isEnabled()
    assert dialog.status_label.text() == '已取消本地合并'
    dialog.start_button.click()
    assert not dialog._stop.is_set()
    calls[1][1](tmp_path / 'result.mp4', '')
    assert dialog.open_button.isEnabled()
    dialog.cancel_button.click()
    assert not dialog.isVisible()
