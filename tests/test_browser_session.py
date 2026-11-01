import os, threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from m3u8_downloader import extractor,utils

@pytest.fixture
def gated_page():
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/session':
                accepted=('HeadlessChrome' not in self.headers.get('User-Agent','')
                          and self.headers.get('Cookie')=='session=allowed'
                          and self.headers.get('Referer')=='https://reference.example/watch')
                body=(f'<script>fetch("/video.m3u8");fetch("http://localhost:{self.server.server_port}/other",{{mode:"no-cors"}})</script>' if accepted else '<title>Login required</title>').encode()
                mime='text/html'
            elif self.path=='/desktop':
                body=(b'<script>fetch("/video.m3u8")</script>' if 'HeadlessChrome' not in self.headers.get('User-Agent','') else b'<title>Unsupported browser</title>')
                mime='text/html'
            elif self.path=='/other':
                received.append(self.headers.get('Cookie',''));body=b'ok';mime='text/plain'
            else:body=b'#EXTM3U\n#EXT-X-ENDLIST\n';mime='application/vnd.apple.mpegurl'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',received
    server.shutdown();server.server_close();thread.join()

@pytest.mark.parametrize('worker',[False,True])
def test_deep_mode_uses_session_headers_and_scopes_cookie_to_page_origin(gated_page,worker):
    base,received=gated_page
    session=utils.create_http_session(10,no_proxy=True)
    session.headers.update({'Cookie':'session=allowed','Referer':'https://reference.example/watch'})
    try:
        candidates,_=extractor.extract_m3u8_from_page_with_title(base+'/session',session=session,deep=True,estimate=False,no_proxy=True,stop_event=threading.Event() if worker else None)
        assert [c.url for c in candidates]==[base+'/video.m3u8']
        assert received and all(not cookie for cookie in received)
    finally:session.close()

def test_default_deep_browser_presents_a_desktop_browser(gated_page):
    base,_=gated_page
    candidates,_=extractor.extract_m3u8_from_page_with_title(base+'/desktop',deep=True,estimate=False,no_proxy=True)
    assert [c.url for c in candidates]==[base+'/video.m3u8']

def test_service_does_not_reuse_a_previous_tasks_login_cookie(gated_page):
    base,_=gated_page
    session=utils.create_http_session(10,no_proxy=True)
    session.headers.update({'Cookie':'session=allowed','Referer':'https://reference.example/watch'})
    try:
        candidates,_=extractor.extract_m3u8_from_page_with_title(base+'/session',session=session,deep=True,estimate=False,no_proxy=True,stop_event=threading.Event())
        assert len(candidates)==1
        session.headers.pop('Cookie')
        with pytest.raises(extractor.NoCandidateFoundError):
            extractor.extract_m3u8_from_page_with_title(base+'/session',session=session,deep=True,estimate=False,no_proxy=True,stop_event=threading.Event())
    finally:session.close()


def test_standalone_worker_receives_cookie_without_command_line_exposure(gated_page):
    import json,subprocess,sys
    base,_=gated_page
    env=os.environ.copy()
    env['M3U8_DEEP_BROWSER_HEADERS']=json.dumps({'Cookie':'session=allowed','Referer':'https://reference.example/watch'})
    result=subprocess.run([sys.executable,'-m','m3u8_downloader.deep_worker','--url',base+'/session','--wait-ms','3500'],env=env,capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['urls']==[base+'/video.m3u8']
    assert 'session=allowed' not in result.stdout+result.stderr

@pytest.mark.parametrize('worker',[False,True])
def test_json_cookie_export_is_used_by_deep_extraction(gated_page,worker):
    import json
    from m3u8_downloader.cookie_input import apply_cookie_input
    base,received=gated_page
    session=utils.create_http_session(10,no_proxy=True)
    session.headers['Referer']='https://reference.example/watch'
    apply_cookie_input(session,json.dumps({'url':base,'cookies':[{'domain':'127.0.0.1','name':'session','value':'allowed','hostOnly':True,'path':'/','secure':False,'httpOnly':True,'sameSite':'unspecified'}]}),base+'/session')
    try:
        candidates,_=extractor.extract_m3u8_from_page_with_title(base+'/session',session=session,deep=True,estimate=False,no_proxy=True,stop_event=threading.Event() if worker else None)
        assert [c.url for c in candidates]==[base+'/video.m3u8']
        assert received and not any(received)
    finally:session.close()
