"""Owned browser-only codec/playback preflight; not workflow/release evidence."""
import base64,contextlib,json,math,os,struct,subprocess,tempfile,time,wave
from pathlib import Path
from urllib.request import urlopen
from record_narrated_demo import Recorder,stop_browser,write,sha
from verify_pilot_browser import DevTools
root=Path.cwd();output=root/'artifacts/narrated-media-preflight-2026-10-04-001'
write(output/'source-snapshot.json',{n:(root/n).read_text() for n in ['scripts/record_narrated_demo.py','scripts/narrated_demo_encoder.js','scripts/verify_pilot_browser.py']})
process=recorder=client=None
report={'status':'failed','scope':'Browser-only synthetic-tone media preflight; no parsing, workflow, human speech or timing acceptance.'}
try:
 with tempfile.TemporaryDirectory(prefix='docwork-codec-preflight-') as temporary:
  profile=Path(temporary)
  process=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  try:
   deadline=time.monotonic()+15
   while not (profile/'DevToolsActivePort').exists() and time.monotonic()<deadline:time.sleep(.1)
   port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
   with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:target=json.load(response)[0]['id']
   client=DevTools(port,target);client.evaluate("document.body.textContent='Media codec preflight — no workflow or human claim'")
   recorder=Recorder(port,target,output);client.call('Page.bringToFront')
   wav=output/'tone.wav'
   with wave.open(str(wav),'wb') as stream:
    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
    stream.writeframes(b''.join(struct.pack('<h',int(10000*math.sin(2*math.pi*440*n/24000))) for n in range(24000)))
   recorder.evaluate(f'demo.scene("Synthetic tone preflight",{json.dumps(base64.b64encode(wav.read_bytes()).decode())})')
   time.sleep(2)
   report['recording']=recorder.finish();report['status']='passed'
  finally:
   if recorder:recorder.close()
   if client:client.close()
   report['chrome_stopped']=stop_browser(process);process=None
except Exception as error:report['failure']={'type':type(error).__name__,'detail':str(error)}
finally:
 if process:report['chrome_stopped']=stop_browser(process)
 write(output/'report.json',report)
 print(json.dumps(report))
