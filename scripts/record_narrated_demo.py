"""Record an uncut live rules workflow with disclosed synthesized narration."""
from __future__ import annotations

import argparse
import array
import base64
import contextlib
import hashlib
import html
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from urllib.request import Request, urlopen

from docwork.intake import IntakeStore
from docwork.performance import source_hashes
from docwork.web import ReviewServer
from docwork.worker import PARSER_IMAGE, _docker_image_id
from verify_pilot_browser import DevTools
from verify_review_browser import enter

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SOURCES = {'src/docwork/access.py', 'src/docwork/web.py', 'src/docwork/intake.py',
                    'src/docwork/supervisor.py', 'src/docwork/lifecycle.py', 'src/docwork/worker.py'}
VERSION = 'continuous-narrated-demo-v1'
SCENES = (
    ('intro', 'A local invoice workflow', 'This is a continuous, scripted demonstration of the local document intelligence workbench. The invoice is fictional, the voice is synthesized, and the product uses real rules parsing. This is not a human review study or a performance benchmark.'),
    ('upload', 'Upload and automatic processing', 'I upload the original invoice through the browser. The supervised serial worker parses it in a network denied Docker container and creates an unapproved suggestion. Rules remain the default extractor.'),
    ('source', 'Inspect cited OCR and review rotation', 'Selecting the invoice number shows its cited OCR line on the source. I rotate the review view and its highlight together, then restore the view. A matching citation does not prove semantic correctness; the reviewer must inspect the document.'),
    ('edit', 'A correction creates a new revision', 'I demonstrate a correction using the native edit controls, then restore the printed value in another revision. The original suggestion is preserved. An attempted export before approval is refused.'),
    ('approve', 'Approval binds the current revision', 'The server establishes the local reviewer identity. I approve the current revision. This is an automated fixture approval for the demonstration, not a human quality assessment. Approval never proves that the values are correct.'),
    ('export', 'Download immutable JSON and CSV', 'The approved revision now produces JSON and CSV downloads. I verify all downloaded bytes against the export store. CSV text is protected against spreadsheet formula interpretation, and earlier exports remain immutable.'),
    ('restart', 'Restart preserves the committed review', 'I stop this disposable service and reopen the same database and object store. The committed edits and approval survive, and the saved export remains byte identical. Separate crash drills cover incomplete transactions and stale worker fencing.'),
    ('reprocess', 'Reprocessing requires fresh approval', 'Reprocessing creates a new extraction identity and revision using the verified parser checkpoint. The earlier approved export survives, but the new candidate needs fresh approval. Review authority is separate from extraction.'),
    ('limits', 'Measured results and release limits', 'On the declared synthetic invoice comparison, the optional local model regresses against rules and is not promoted. The author pilot retains two errors in approved records. Genuine scanner validation, manual semantic assessments and remaining release gates are still pending. This project makes no time saved or unattended approval claim.'),
)
EXTRA = ('scripts/record_narrated_demo.py', 'scripts/narrated_demo_encoder.js',
         'scripts/verify_pilot_browser.py', 'scripts/verify_review_browser.py')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def narration_info(path):
    with wave.open(str(path), 'rb') as stream:
        require(stream.getcomptype() == 'NONE' and stream.getsampwidth() == 2, 'Narration must be uncompressed sixteen-bit PCM')
        frames, rate, channels = stream.getnframes(), stream.getframerate(), stream.getnchannels()
        require(frames > 0 and rate > 0 and channels in (1, 2), 'Invalid narration format')
        data = stream.readframes(frames)
    require(len(data) == frames * channels * 2, 'Truncated narration')
    pcm = array.array('h', data)
    if sys.byteorder != 'little':
        pcm.byteswap()
    require(max(pcm) - min(pcm) >= 256, 'Silent/constant narration')
    return {'seconds': frames / rate, 'sample_rate': rate, 'channels': channels, 'sha256': sha(path)}


def group_running(group):
    rows = subprocess.check_output(['ps', '-axo', 'pgid=,stat='], text=True, timeout=5)
    return any(int(pid) == group and not status.startswith('Z')
               for pid, status in (line.split() for line in rows.splitlines()))


def stop_browser(process):
    """Stop/reap the owned session, including surviving non-zombie helpers."""
    if process is None:
        return True
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while group_running(process.pid) and time.monotonic() < deadline:
        process.poll()
        time.sleep(.1)
    if group_running(process.pid):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)
    deadline = time.monotonic() + 5
    while group_running(process.pid) and time.monotonic() < deadline:
        time.sleep(.1)
    return not group_running(process.pid)


class Recorder:
    def __init__(self, port, target, output):
        self.capture = DevTools(port, target)
        with urlopen(Request(f'http://127.0.0.1:{port}/json/new?about:blank', method='PUT'), timeout=10) as response:
            encoder_target = json.load(response)['id']
        self.encoder = DevTools(port, encoder_target)
        self.encoder.evaluate((ROOT / 'scripts/narrated_demo_encoder.js').read_text())
        self.output, self.lock, self.stop = output, threading.RLock(), threading.Event()
        self.frames, self.errors, self.scenes = [], [], []
        self.origin = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def evaluate(self, expression):
        with self.lock:
            return self.encoder.evaluate(expression)

    def run(self):
        while not self.stop.is_set():
            try:
                data = self.capture.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']
                self.evaluate(f'demo.frame({json.dumps(data)})')
                self.frames.append(time.monotonic() - self.origin)
            except Exception as error:
                self.errors.append({'elapsed_seconds': time.monotonic() - self.origin, 'type': type(error).__name__})
            self.stop.wait(.25)

    def scene(self, name, action=lambda: None):
        _, caption, transcript = next(scene for scene in SCENES if scene[0] == name)
        path = self.output / 'narration' / f'{name}.wav'
        start = time.monotonic() - self.origin
        duration = self.evaluate(f'demo.scene({json.dumps(caption)},{json.dumps(base64.b64encode(path.read_bytes()).decode())})')
        action()
        deadline = time.monotonic() + duration + 10
        while self.evaluate('demo.active()'):
            require(time.monotonic() < deadline, 'Narration did not finish')
            time.sleep(.1)
        self.scenes.append({'id': name, 'caption': caption, 'transcript': transcript,
                            'started_seconds': start, 'finished_seconds': time.monotonic() - self.origin,
                            'audio_seconds': duration})

    def finish(self):
        self.stop.set();self.thread.join(timeout=15)
        require(not self.thread.is_alive(), 'Viewport observer did not stop')
        write(self.output/'recording-events.json', {'frames_seconds': self.frames, 'query_errors': self.errors,
              'scenes': self.scenes, 'seconds': time.monotonic() - self.origin})
        self.encoder.call('Page.bringToFront')
        self.encoder.socket.settimeout(30)
        playback = self.evaluate('demo.finish()')
        require(playback['width'] == 1440 and playback['height'] == 1160 and
                playback['current_time'] > 0 and playback.get('audio_decoded_bytes', 0) > 0 and
                playback.get('video_decoded_bytes', 0) > 0, 'Generated audio/video playback failed')
        with (self.output / 'demo.webm').open('wb') as stream:
            for offset in range(0, playback['bytes'], 192 * 1024):
                stream.write(base64.b64decode(self.evaluate(f'demo.chunk({offset},196608)')))
        return {'method': 'Uncut wall-rate canvas/MediaRecorder recording of live Chrome viewport samples with synthesized PCM narration; no screenshot montage or processing/human-time claim.',
                'frames_seconds': self.frames, 'query_errors': self.errors, 'scenes': self.scenes,
                'seconds': time.monotonic() - self.origin, 'playback': playback}

    def close(self):
        self.stop.set();self.thread.join(timeout=15)
        self.capture.close();self.encoder.close()


def validate_recording(recording):
    require([scene['id'] for scene in recording['scenes']] == [scene[0] for scene in SCENES], 'Incomplete narration schedule')
    require(recording['query_errors'] == [], 'Viewport query failed')
    times = recording['frames_seconds']
    require(len(times) > 30 and all(math.isfinite(t) and t >= 0 for t in times) and
            all(a <= b for a, b in zip(times, times[1:])), 'Invalid continuous viewport timeline')
    require(max(b-a for a,b in zip(times,times[1:])) <= 2, 'Continuous viewport gap exceeds two seconds')
    require(times[0] <= 1 and times[-1] >= recording['scenes'][-1]['finished_seconds'] - 1, 'Viewport omits start or end')
    for scene, expected in zip(recording['scenes'], SCENES):
        require(scene['caption'] == expected[1] and scene['transcript'] == expected[2] and
                0 <= scene['started_seconds'] < scene['finished_seconds'] and scene['audio_seconds'] > 0 and
                any(scene['started_seconds'] <= t <= scene['finished_seconds'] for t in times), 'Invalid scene/audio/viewport coverage')
    require(all(a['finished_seconds'] <= b['started_seconds'] for a,b in zip(recording['scenes'],recording['scenes'][1:])), 'Narration scenes overlap')


def verify(output):
    report = json.loads((output / 'report.json').read_text())
    require(report['version'] == VERSION and report['status'] == 'passed', 'Incomplete narrated demo')
    snapshot = json.loads((output / 'source-snapshot.json').read_text())
    require(REQUIRED_SOURCES | set(EXTRA) <= set(snapshot), 'Source inventory omits workflow/recorder')
    require(all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in snapshot), 'Unsafe source name')
    require({name: hashlib.sha256(text.encode()).hexdigest() for name,text in snapshot.items()} == report['source_sha256'], 'Source snapshot differs')
    validate_recording(report['recording'])
    require('storage_warning_absent' not in report or report['storage_warning_absent'] is True, 'Unexpected storage warning')
    inventory = report['artifacts']
    actual = {str(path.relative_to(output)): sha(path) for path in output.rglob('*') if path.is_file() and path.name != 'report.json'}
    require(not any(path.is_symlink() for path in output.rglob('*')) and actual == inventory, 'Demo inventory differs')
    require((output/'demo.webm').read_bytes()[:4] == b'\x1aE\xdf\xa3', 'Invalid WebM artifact')
    require(sha(output/'input.png') == report['input_sha256'], 'Demo input differs')
    require(report['checks'] == ['live_rules_upload', 'source_rotation', 'versioned_edits_and_export_refusal',
            'revision_approval', 'byte_verified_json_csv', 'restart_preserves_committed_review',
            'checkpoint_reprocess_requires_approval'], 'Incomplete workflow checks')
    for name, _, _ in SCENES:
        require(narration_info(output/'narration'/f'{name}.wav') == report['narration'][name], 'Narration metadata differs')
    require(report['cleanup'] == {'chrome_stopped': True, 'servers_stopped': True, 'scratch_removed': True}, 'Owned cleanup incomplete')
    return {'status': 'verified', 'checks': len(report['checks']), 'scenes': len(SCENES), 'seconds': report['recording']['seconds'],
            'source_current': all((ROOT/name).is_file() and sha(ROOT/name)==value for name,value in report['source_sha256'].items())}


def record(output, chrome, voice):
    require(not output.exists() and chrome.is_file(), 'Choose a new output and installed Chrome')
    output.mkdir(parents=True);(output/'narration').mkdir()
    (output/'input.png').write_bytes((ROOT/'samples/clean.png').read_bytes())
    paths = {*source_hashes(ROOT), *EXTRA}
    snapshot = {name: (ROOT/name).read_text() for name in sorted(paths)}
    write(output/'source-snapshot.json', snapshot)
    report = {'version': VERSION, 'status': 'failed', 'source_sha256': {n:hashlib.sha256(t.encode()).hexdigest() for n,t in snapshot.items()},
              'parser_image_id': _docker_image_id(PARSER_IMAGE), 'input_sha256': sha(ROOT/'samples/clean.png'),
              'narration_voice': voice or 'system_default', 'narration': {}, 'checks': [],
              'scope': 'Live rules parsing and scripted browser actions on one fictional clean fixture; synthesized voice, automated fixture approval, clean service restart. No model inference, physical scan, human review/semantic assessment, machine power-loss, or controlled performance evidence.'}
    process = recorder = client = server = None
    thread = None
    directory = None
    try:
        for name, _, text in SCENES:
            command = ['/usr/bin/say', '-r', '165', '--file-format=WAVE', '--data-format=LEI16@24000', '-o', str(output/'narration'/f'{name}.wav')]
            if voice: command += ['-v', voice]
            subprocess.run([*command, text], check=True, capture_output=True, timeout=60)
            report['narration'][name] = narration_info(output/'narration'/f'{name}.wav')
        directory = Path(tempfile.mkdtemp(prefix='docwork-narrated-demo-'))
        def start_server():
            store = IntakeStore(directory/'review.sqlite', directory/'objects')
            service = ReviewServer(('127.0.0.1',0), store, background_processing=True)
            worker = threading.Thread(target=service.serve_forever, daemon=True);worker.start()
            return service, worker, store
        server, thread, store = start_server()
        profile = directory/'chrome'
        process = subprocess.Popen([str(chrome),'--headless','--disable-gpu','--disable-background-networking',
            '--disable-sync','--no-first-run','--no-default-browser-check','--autoplay-policy=no-user-gesture-required',
            '--remote-debugging-port=0',f'--user-data-dir={profile}','--window-size=1440,1000','about:blank'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+15
        while not (profile/'DevToolsActivePort').exists() and time.monotonic()<deadline:time.sleep(.1)
        port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
        with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=10) as response:
            target=next(t['id'] for t in json.load(response) if t['type']=='page')
        client=DevTools(port,target);client.call('Page.enable')
        client.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
        def navigate():
            client.call('Page.navigate',{'url':f'{server.origin}/?token={server.token}'})
            client.wait("typeof state!=='undefined' && state.background")
        navigate()
        require(client.evaluate("$('actor').readOnly && $('actor').value.startsWith('local:')"),'Reviewer identity unavailable')
        recorder=Recorder(port,target,output)
        client.call('Page.bringToFront')
        client.wait('!document.hidden')
        recorder.scene('intro')
        def upload():
            node=client.call('DOM.getDocument')['root']['nodeId']
            node=client.call('DOM.querySelector',{'nodeId':node,'selector':'#upload'})['nodeId']
            client.call('DOM.setFileInputFiles',{'nodeId':node,'files':[str(ROOT/'samples/clean.png')]})
            enter(client,'#upload-button');client.wait("state.detail!==null && !$('page-canvas').hidden")
        recorder.scene('upload',upload)
        document=client.evaluate('state.selectedId');original=store.get(document)
        require(original['extraction']['profile']=='ocr_rules' and original['revision']==1,'Fresh rules upload failed')
        report['checks'].append('live_rules_upload')
        def source():
            client.evaluate("document.querySelector('[data-path=\"fields.invoice_number\"]').click()")
            client.wait("$('highlights').children.length>0")
            enter(client,'#rotate-right');client.wait("$('rotation-label').textContent==='90°'")
            time.sleep(2);enter(client,'#rotate-left');client.wait("$('rotation-label').textContent==='0°'")
        recorder.scene('source',source);report['checks'].append('source_rotation')
        printed=original['record']['fields']['invoice_number']['value']
        def edit():
            enter(client,'[data-path="fields.invoice_number"]');client.wait("document.activeElement.id==='edit-value'")
            client.evaluate("$('edit-value').select()");client.call('Input.insertText',{'text':printed+'-DEMO'})
            enter(client,'#save-edit');client.wait('state.detail.revision===2')
            require(store.get(document,1)['record']==original['record'],'Original suggestion changed')
            enter(client,'#export-json');client.wait("$('notice').classList.contains('error')")
            client.evaluate(f"$('edit-value').value={json.dumps(printed)}");enter(client,'#save-edit');client.wait('state.detail.revision===3')
        recorder.scene('edit',edit);report['checks'].append('versioned_edits_and_export_refusal')
        recorder.scene('approve',lambda:enter(client,'#approve-button'));client.wait('state.detail.approval!==null')
        approved=store.get(document);require(approved['approval']['revision']==3,'Approval has wrong revision')
        report['review_state']={'document_id':document,'initial_record_sha256':hashlib.sha256(json.dumps(original['record'],sort_keys=True).encode()).hexdigest(),
            'approved_record_sha256':hashlib.sha256(json.dumps(approved['record'],sort_keys=True).encode()).hexdigest(),
            'approved_revision':3}
        report['checks'].append('revision_approval');saved_files={}
        def export():
            for kind in ('json','csv'):
                enter(client,f'#export-{kind}')
                filename='invoice.json' if kind=='json' else 'header.csv'
                client.wait(f"$('downloads').querySelector('a')?.textContent==={json.dumps(filename)}")
                items=client.evaluate("Promise.all([...$('downloads').querySelectorAll('a')].map(async link=>{const r=await fetch(link.href);return {name:link.textContent,ok:r.ok,bytes:Array.from(new Uint8Array(await r.arrayBuffer()))}}))")
                require({v['name'] for v in items}==({'invoice.json'} if kind=='json' else {'header.csv','line-items.csv'}),'Export files missing')
                for item in items:
                    data,_=store.exported_file(document,3,kind,item['name'])
                    require(item['ok'] and bytes(item['bytes'])==data,'Downloaded bytes differ');saved_files[(kind,item['name'])]=data
        recorder.scene('export',export);report['checks'].append('byte_verified_json_csv')
        report['export_sha256']={kind+'/'+name:hashlib.sha256(data).hexdigest() for (kind,name),data in saved_files.items()}
        def restart():
            nonlocal server,thread,store
            server.shutdown();server.server_close();thread.join(timeout=10)
            require(not thread.is_alive(),'Old service did not stop')
            server,thread,store=start_server();navigate();client.wait('state.detail!==null')
            current=store.get(document)
            require(current['record']==approved['record'] and current['approval']==approved['approval'],'Committed review lost')
            for (kind,name),data in saved_files.items():require(store.exported_file(document,3,kind,name)[0]==data,'Export changed across restart')
        recorder.scene('restart',restart);report['checks'].append('restart_preserves_committed_review')
        def reprocess():
            enter(client,'#reprocess-button');client.wait('state.detail!==null && state.detail.revision===4')
            require(store.get(document)['approval'] is None,'New extraction inherits approval')
            with store._connect() as db:
                require(db.execute("SELECT count(*) FROM review_events WHERE document_id=? AND kind='parser_checkpoint_reused'",(document,)).fetchone()[0]==1,'Parser checkpoint not reused')
            for (kind,name),data in saved_files.items():require(store.exported_file(document,3,kind,name)[0]==data,'Historical export changed')
        recorder.scene('reprocess',reprocess);report['checks'].append('checkpoint_reprocess_requires_approval')
        report['review_state'].update(final_revision=4,final_approval=None,checkpoint_reused=True)
        recorder.scene('limits')
        require(not client.evaluate("document.getElementById('notice')?.textContent.includes('Unexpected artifact entry')"),
                'Unexpected storage inventory warning during normal polling')
        report['storage_warning_absent'] = True
        (output/'final.png').write_bytes(base64.b64decode(client.call('Page.captureScreenshot',{'format':'png'})['data']))
        report['recording']=recorder.finish();validate_recording(report['recording'])
        recorder.close();recorder=None;client.close();client=None
        require(stop_browser(process), 'Chrome helpers did not stop')
        server.shutdown();server.server_close();thread.join(timeout=10)
        require(not thread.is_alive(),'Service did not stop')
        report['cleanup']={'chrome_stopped':process.poll() is not None,'servers_stopped':True,'scratch_removed':False}
        process=server=None
        require(all((ROOT/n).read_text()==t for n,t in snapshot.items()),'Source changed during recording')
        transcript='\n\n'.join(f'{caption}\n{text}' for _,caption,text in SCENES)
        (output/'transcript.txt').write_text(transcript+'\n')
        (output/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Live narrated workbench demo</title><style>body{max-width:1100px;margin:auto;padding:24px;font:16px/1.6 system-ui;background:#f4f7fa;color:#192b3c}video{width:100%}a{color:#245b88}</style><h1>A recoverable invoice review workflow</h1><p>Continuous live Chrome viewport recording, real rules parsing, scripted actions on a fictional invoice and synthesized narration. Automated fixture approvals supply no human-quality results. Experimental release; scan/manual/memory/final-release gaps remain.</p><video controls preload="metadata" src="demo.webm"></video><p><a href="transcript.txt">Narration transcript</a> · <a href="report.json">Source-bound verification</a></p><pre style="white-space:pre-wrap">'+html.escape(transcript)+'</pre></html>')
        report['status']='passed'
    except Exception as error:
        report['failure']={'type':type(error).__name__,'detail':str(error)}
    finally:
        if recorder:
            with contextlib.suppress(Exception):recorder.close()
        if client:
            with contextlib.suppress(Exception):client.close()
        browser_stopped = stop_browser(process)
        if server:
            server.shutdown();server.server_close()
            if thread:thread.join(timeout=10)
        cleanup = report.setdefault('cleanup', {})
        cleanup['chrome_stopped'] = browser_stopped
        cleanup['servers_stopped'] = thread is None or not thread.is_alive()
        try:
            if directory is not None and directory.exists():
                shutil.rmtree(directory)
            cleanup['scratch_removed'] = directory is None or not directory.exists()
        except OSError as error:
            cleanup['scratch_removed'] = False
            report['cleanup_failure'] = {'type': type(error).__name__, 'detail': str(error)}
        if not all(cleanup.values()):
            report['status'] = 'failed'
        report['artifacts']={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file() and p.name!='report.json'}
        write(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--verify',action='store_true');parser.add_argument('--voice')
    parser.add_argument('--chrome',type=Path,default=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
    args=parser.parse_args()
    result=verify(args.output_dir.resolve()) if args.verify else record(args.output_dir.resolve(),args.chrome,args.voice)
    print(json.dumps(result,indent=2));return 0 if result['status'] in ('passed','verified') else 2

if __name__=='__main__':raise SystemExit(main())
