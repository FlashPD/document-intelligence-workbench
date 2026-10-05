"""Offline checks for incomplete/cut narrated-demo evidence; no browser or speech."""
import copy
import hashlib
import os
import subprocess
import signal
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import record_narrated_demo as demo


def recording():
    return {'frames_seconds': [n/4 for n in range(361)], 'query_errors': [],
            'seconds': 90.5,
            'scenes': [{'id': name, 'caption': caption, 'transcript': text,
                        'started_seconds': i*10, 'finished_seconds': (i+1)*10,
                        'audio_seconds': 1}
                       for i,(name,caption,text) in enumerate(demo.SCENES)]}


class NarratedDemoTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX session ownership')
    def test_owned_browser_session_stops_helpers(self):
        # The signal handler reaps its child only if the whole group receives TERM.
        code=("import subprocess,sys,time,signal\n"
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
              "def stopped(*_):\n p.wait(timeout=5)\n sys.exit(0)\n"
              "signal.signal(signal.SIGTERM,stopped)\nprint(p.pid,flush=True)\ntime.sleep(30)\n")
        process=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True,start_new_session=True)
        try:
            child=int(process.stdout.readline())
            self.assertGreater(child,0)
            # Replace ps observation so this test runs under restrictive host sandboxes.
            # The actual process-group signal and parent/child exits remain real.
            with patch.object(demo,'group_running',lambda _:process.poll() is None):
                self.assertTrue(demo.stop_browser(process))
            self.assertEqual(process.returncode,0)
            with self.assertRaises(ProcessLookupError):os.kill(child,0)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=5)
            process.stdout.close()

    def test_window_schedule_rejects_cuts_omission_overlap_and_errors(self):
        value=recording();demo.validate_recording(value)
        changes=[lambda v:v['scenes'].pop(), lambda v:v['query_errors'].append({'type':'TimeoutError'}),
                 lambda v:v.update(frames_seconds=[t for t in v['frames_seconds'] if not 12<t<18]),
                 lambda v:v['scenes'][2].update(started_seconds=19),
                 lambda v:v['scenes'][1].update(audio_seconds=0),
                 lambda v:v.update(frames_seconds=v['frames_seconds'][8:]),
                 lambda v:v.update(frames_seconds=v['frames_seconds'][:-12]),
                 lambda v:v['scenes'][0].update(transcript='Claim actual human review')]
        for change in changes:
            with self.subTest(change=change):
                modified=copy.deepcopy(value);change(modified)
                with self.assertRaises(ValueError):demo.validate_recording(modified)

    def test_audio_rejects_silence_and_retains_pcm_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'audio.wav'
            for frames, accepted in [(b'\x00\x00'*24000,False),(b'\x01\x00'*24000,False),(b'\x00\x01\x00\xff'*12000,True)]:
                with wave.open(str(path),'wb') as stream:
                    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(frames)
                if accepted:
                    self.assertEqual(demo.narration_info(path)['seconds'],1)
                else:
                    with self.assertRaises(ValueError):demo.narration_info(path)

    def test_archive_requires_artifacts_checks_input_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary);(output/'narration').mkdir()
            snapshot={name:'test-only source snapshot' for name in demo.REQUIRED_SOURCES | set(demo.EXTRA)}
            demo.write(output/'source-snapshot.json',snapshot)
            (output/'input.png').write_bytes(b'fictional test input')
            (output/'demo.webm').write_bytes(b'\x1aE\xdf\xa3test-only-not-playable')
            voices={}
            for name,_,_ in demo.SCENES:
                path=output/'narration'/f'{name}.wav'
                with wave.open(str(path),'wb') as stream:
                    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(b'\x00\x01\x00\xff'*12000)
                voices[name]=demo.narration_info(path)
            report={'version':demo.VERSION,'status':'passed','source_sha256':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in snapshot.items()},'recording':recording(),
                    'input_sha256':demo.sha(output/'input.png'),'narration':voices,
                    'checks':['live_rules_upload','source_rotation','versioned_edits_and_export_refusal',
                              'revision_approval','byte_verified_json_csv','restart_preserves_committed_review',
                              'checkpoint_reprocess_requires_approval'],
                    'cleanup':{'chrome_stopped':True,'servers_stopped':True,'scratch_removed':True},
                    'artifacts':{str(p.relative_to(output)):demo.sha(p) for p in output.rglob('*') if p.is_file()}}
            demo.write(output/'report.json',report)
            # Saved audit validates the recorded claims/integrity; live playback belongs to record().
            self.assertEqual(demo.verify(output)['status'],'verified')
            for change in [lambda r:r['checks'].pop(),lambda r:r['cleanup'].update(scratch_removed=False),
                           lambda r:r.update(input_sha256='0'*64),lambda r:r.update(status='failed'),
                           lambda r:r.update(storage_warning_absent=False)]:
                modified=copy.deepcopy(report);change(modified);demo.write(output/'report.json',modified)
                with self.assertRaises(ValueError):demo.verify(output)
            demo.write(output/'report.json',report);(output/'narration/intro.wav').unlink()
            with self.assertRaises(ValueError):demo.verify(output)


if __name__=='__main__':unittest.main()
