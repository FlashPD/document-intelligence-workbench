"""Independent local-file playback/seek/audio review of the produced WebM."""
import base64,functools,http.server,json,subprocess,tempfile,threading,time
from pathlib import Path
from urllib.request import urlopen
from record_narrated_demo import stop_browser,write,sha
from verify_pilot_browser import DevTools
root=Path.cwd();bundle=root/'evals/narrated-demo-2026-10-04/final';output=root/'artifacts/narrated-playback-review-2026-10-04-003'
report={'status':'failed','video_sha256':sha(bundle/'demo.webm'),'producer_report_sha256':sha(bundle/'report.json')}
class Handler(http.server.SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(bundle)))
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
process=client=None
try:
 with tempfile.TemporaryDirectory(prefix='docwork-playback-review-') as temporary:
  profile=Path(temporary)
  process=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  try:
   deadline=time.monotonic()+15
   while not (profile/'DevToolsActivePort').exists() and time.monotonic()<deadline:time.sleep(.1)
   port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
   with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:target=json.load(response)[0]['id']
   client=DevTools(port,target);client.socket.settimeout(30);client.call('Page.enable');client.call('Page.bringToFront')
   report['browser']=client.call('Browser.getVersion')
   client.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1160,'deviceScaleFactor':1,'mobile':False})
   report['navigation']=client.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/index.html'})
   time.sleep(1);report['document']=client.evaluate("({url:location.href,title:document.title,text:document.body.innerText.slice(0,1200)})");client.wait("!!document.querySelector('video')");client.evaluate("(async()=>{const v=document.querySelector('video');v.muted=true;await v.play();v.pause()})()");client.wait("document.querySelector('video')?.readyState>=2")
   client.evaluate("window.reviewVideo=document.querySelector('video');document.body.replaceChildren(reviewVideo);document.body.style='margin:0;padding:0;max-width:none';reviewVideo.style='width:1440px;height:1160px;object-fit:contain';reviewVideo.muted=true")
   producer=json.loads((bundle/'report.json').read_text());report['frames']=[]
   for name in ['source','approve','reprocess']:
    scene=next(s for s in producer['recording']['scenes'] if s['id']==name);target_time=scene['finished_seconds']-.5
    observed=client.evaluate(f"(async()=>{{reviewVideo.pause();const seeked=new Promise((resolve,reject)=>{{reviewVideo.onseeked=resolve;reviewVideo.onerror=()=>reject(Error('Seek failed'))}});reviewVideo.currentTime={target_time};await seeked;return {{current_time:reviewVideo.currentTime,width:reviewVideo.videoWidth,height:reviewVideo.videoHeight}}}})()")
    assert abs(observed['current_time']-target_time)<.1 and observed['width']==1440 and observed['height']==1160
    (output/f'{name}.png').write_bytes(base64.b64decode(client.call('Page.captureScreenshot',{'format':'png'})['data']))
    report['frames'].append({'scene':name,'target_seconds':target_time,**observed})
   report['decoded_audio']=client.evaluate("(async()=>{const bytes=await (await fetch('demo.webm')).arrayBuffer();const context=new AudioContext();const buffer=await context.decodeAudioData(bytes);let peak=0;const samples=buffer.getChannelData(0);for(let i=0;i<samples.length;i+=100)peak=Math.max(peak,Math.abs(samples[i]));const info={duration_seconds:buffer.duration,channels:buffer.numberOfChannels,sample_rate:buffer.sampleRate,peak};await context.close();return info})()")
   assert report['decoded_audio']['duration_seconds']>=sum(n['seconds'] for n in producer['narration'].values())-1 and report['decoded_audio']['peak']>.01
   report['status']='passed'
  finally:
   if client:client.close()
   report['chrome_stopped']=stop_browser(process);process=None
except Exception as error:report['failure']={'type':type(error).__name__,'detail':str(error)}
finally:
 if process:report['chrome_stopped']=stop_browser(process)
 server.shutdown();server.server_close();thread.join(timeout=10)
 report['server_stopped']=not thread.is_alive();write(output/'report.json',report)
 print(json.dumps(report))
