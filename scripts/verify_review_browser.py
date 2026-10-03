"""Verify native review controls and record a scripted fictional-fixture demo.

Uses saved development OCR, an isolated temporary store, and installed Chrome.
Never attaches to a user's server, starts inference, or measures human time.
"""

from __future__ import annotations

import argparse
import base64
import html
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

from docwork.geometry import DisplayTransform, PixelBox
from docwork.demo_replay import CASES as REPLAY_CASES, prepare_replay
from docwork.intake import IntakeStore
from docwork.model_runtime import file_hash
from docwork.pilot_bundle import SOURCES, prepare_pilot
from docwork.web import ReviewServer
from verify_pilot_browser import DevTools

CASES = ("inv-f02-02", "inv-f01-12", "inv-f05-30", "inv-f06-04")
CHECKS = {"recorded provenance", "keyboard field navigation", "native page selector", "native action buttons", "source stays visible during correction",
          "correction creates revision", "stale revision rejected", "unapproved export blocked",
          "approval bound to revision", "JSON and CSV downloads", "rotated page pixels and highlights",
          "page-specific rotation", "field jumps to source page", "missing evidence visible", "responsive layout"}


def key(client, name, code, virtual):
    for kind in ("keyDown", "keyUp"):
        event = {"type": kind, "key": name, "code": code, "windowsVirtualKeyCode": virtual}
        if name == "Enter" and kind == "keyDown":
            event.update(text="\r", unmodifiedText="\r")
        client.call("Input.dispatchKeyEvent", event)


def focus(client, selector):
    client.evaluate(f"document.querySelector({json.dumps(selector)}).focus()")


def enter(client, selector):
    focus(client, selector)
    key(client, "Enter", "Enter", 13)


def verify_geometry(client, detail, rotation):
    """Compare DOM highlights to the Python geometry contract and raster pixels."""
    number = client.evaluate("state.pageNumber")
    page = detail["pages"][number - 1]
    selected = client.evaluate("state.selectedPath")
    parts = selected.split(".")
    field = detail["record"]["fields"][parts[1]] if parts[0] == "fields" else next(
        row for row in detail["record"]["line_items"] if row["row_id"] == parts[1])[parts[2]]
    spans = [s for s in page["spans"] if s["id"] in field["evidence_ids"] and s["box"]]
    assert spans, "Selected fixture needs cited geometry"
    width, height = page["width_px"], page["height_px"]
    transform = DisplayTransform(width, height, PixelBox(0, 0, width, height), rotation)
    expected = [asdict(transform.box(PixelBox(s["box"]["left"] * width, s["box"]["top"] * height,
                                            s["box"]["right"] * width, s["box"]["bottom"] * height))) for s in spans]
    client.wait(f"!$('page-canvas').hidden && $('rotation-label').textContent === '{rotation}°'")
    observed = client.evaluate("""(() => {
      const canvas = $('page-canvas').getBoundingClientRect();
      return [...$('highlights').children].map(element => {
        const box = element.getBoundingClientRect();
        return {left:(box.left-canvas.left)/canvas.width,top:(box.top-canvas.top)/canvas.height,
                right:(box.right-canvas.left)/canvas.width,bottom:(box.bottom-canvas.top)/canvas.height};
      });
    })()""")
    assert len(observed) == len(expected)
    for actual, target in zip(observed, expected):
        assert all(abs(actual[name] - target[name]) < .004 for name in target), (actual, target)
    # Compare sampled nonwhite source pixels to integer pixel-center rotations.
    pixels = client.evaluate("""(() => {
      const image = state.pageImages.get(state.pageNumber), reference = document.createElement('canvas');
      reference.width=image.naturalWidth; reference.height=image.naturalHeight;
      const source=reference.getContext('2d'); source.drawImage(image,0,0);
      const data=source.getImageData(0,0,reference.width,reference.height).data, points=[];
      for(let y=20;y<reference.height-20 && points.length<12;y+=13)
        for(let x=20;x<reference.width-20 && points.length<12;x+=11){
          const i=(y*reference.width+x)*4;
          if(Math.min(data[i],data[i+1],data[i+2])<170) points.push([x,y,...data.slice(i,i+4)]);
        }
      return {width:$('page-canvas').width,height:$('page-canvas').height,points};
    })()""")
    assert len(pixels["points"]) >= 4
    assert (pixels["width"], pixels["height"]) == transform.display_size, "Fixture must use unscaled canvas"
    targets = []
    for x, y, *color in pixels["points"]:
        px, py = transform.point(x + .5, y + .5)
        targets.append([int(px), int(py), color])
    colors = client.evaluate(f"{json.dumps(targets)}.map(([x,y]) => Array.from($('page-canvas').getContext('2d').getImageData(x,y,1,1).data))")
    assert all(color == target[2] for color, target in zip(colors, targets)), "Rotated raster pixels differ"


def record_demo(client, frames, output):
    """Browser-native WebM encoding of labeled screenshot holds, not live latency."""
    items = [{"caption": f["caption"], "image": base64.b64encode((output / f["path"]).read_bytes()).decode()} for f in frames]
    expression = """(async () => {
      const items=ITEMS, canvas=document.createElement('canvas'); canvas.width=1440; canvas.height=1190;
      const ctx=canvas.getContext('2d'), stream=canvas.captureStream(0), track=stream.getVideoTracks()[0];
      const mime=['video/webm;codecs=vp9','video/webm;codecs=vp8'].find(type=>MediaRecorder.isTypeSupported(type));
      if(!mime) throw new Error('Chrome has no supported WebM encoder');
      const recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:1800000}),chunks=[];
      recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data)};
      const stopped=new Promise((resolve,reject)=>{recorder.onstop=resolve;recorder.onerror=reject});
      recorder.start();
      try {
        for(const item of items){
          const image=new Image(); image.src='data:image/png;base64,'+item.image; await image.decode();
          ctx.fillStyle='#173e2c';ctx.fillRect(0,0,1440,1190);ctx.fillStyle='white';ctx.font='22px sans-serif';
          ctx.fillText(item.caption,24,34);ctx.font='16px sans-serif';
          ctx.fillText('SCRIPTED DEMO · fictional development invoices · saved OCR · no human timing or processing latency',24,65);
          ctx.drawImage(image,0,90,1440,1100);track.requestFrame();
          await new Promise(resolve=>setTimeout(resolve,3000));
        }
      } finally {recorder.stop();await stopped;stream.getTracks().forEach(t=>t.stop())}
      const blob=new Blob(chunks,{type:mime}),bytes=new Uint8Array(await blob.arrayBuffer());
      let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
      return {mime,base64:btoa(binary),nominal_seconds:items.length*3};
    })()""".replace("ITEMS", json.dumps(items))
    result = client.evaluate(expression)
    data = base64.b64decode(result.pop("base64"))
    assert data[:4] == b"\x1aE\xdf\xa3" and len(data) > 1000, "WebM encoder did not produce a container"
    (output / "demo.webm").write_bytes(data)
    # Independently load the produced media and wait for actual playback readiness.
    encoded = base64.b64encode(data).decode()
    playback = client.evaluate("""(async () => {
      const video=document.createElement('video');video.muted=true;
      video.src='data:video/webm;base64,'+DATA;
      await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=()=>reject(new Error('Demo cannot be decoded'));video.load()});
      await video.play();await new Promise(resolve=>setTimeout(resolve,100));video.pause();
      return {width:video.videoWidth,height:video.videoHeight,ready:video.readyState,current_time:video.currentTime};
    })()""".replace("DATA", json.dumps(encoded)))
    assert playback["width"] == 1440 and playback["height"] == 1190 and playback["current_time"] > 0
    return {**result, "playback": playback, "method": "Three-second captioned holds of real Chrome screenshots, assembled with canvas/MediaRecorder. No audio or elapsed processing/review claim."}


def render_demo(frames, recording):
    images = "".join(f'<figure><img src="{html.escape(f["path"], quote=True)}" alt="{html.escape(f["caption"], quote=True)}"><figcaption>{html.escape(f["caption"])}</figcaption></figure>' for f in frames)
    video = '<video controls preload="metadata" src="demo.webm" aria-label="Scripted review workflow demo"></video>' if recording else ""
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Intelligence Workbench — scripted review demo</title><style>body{{max-width:1100px;margin:auto;padding:24px;font:16px/1.6 system-ui;background:#f4f7fa;color:#192b3c}}img,video{{width:100%;height:auto;border:1px solid #ccd9df}}figure{{margin:24px 0}}figcaption{{padding:12px;background:white}}a{{color:#245b88}}</style>
<h1>Review an invoice with its evidence</h1><p>Scripted Chrome demonstration on fictional development invoices using recorded OCR suggestions. No model inference, live parsing, human timing, or productivity claim. The video assembles captioned screenshots; its duration is presentation pacing.</p>
<p><a href="report.json">Verification and artifact hashes</a></p>{video}{images}</html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-recording", action="store_true", help="Verify controls and save screenshots without WebM encoding")
    parser.add_argument("--demo-replay", action="store_true", help="Verify the offline portfolio demo entrypoint and its four cases")
    args = parser.parse_args()
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink() or not args.chrome.is_file():
        parser.error("Use a new output directory and an installed Chrome executable")
    root = Path(__file__).resolve().parents[1]
    cases = tuple(case[0] for case in REPLAY_CASES) if args.demo_replay else CASES
    source_names = (*SOURCES, "scripts/verify_pilot_browser.py", "scripts/verify_review_browser.py", "src/docwork/geometry.py")
    if args.demo_replay:
        source_names += ("src/docwork/demo_replay.py", "src/docwork/cli.py", "Makefile")
    snapshot = {name: (root / name).read_text() for name in source_names}
    output.mkdir(parents=True)
    (output / "source_snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    checked, frames = [], []
    recording = None
    process = client = None
    with tempfile.TemporaryDirectory(prefix="docwork-review-browser-") as temporary:
        directory = Path(temporary)
        replay = prepare_replay(root, directory / "fixtures") if args.demo_replay else None
        protocol = None if replay else prepare_pilot(root, root / "datasets/invoices-v1/manifest.json",
                                 root / "evals/invoice-freeze-2026-10-03/development", directory / "fixtures", document_ids=cases)
        store = IntakeStore(directory / "fixtures/review.sqlite", directory / "fixtures/objects")
        ids = {doc["corpus_id"]: doc["document_id"] for doc in (replay["cases"] if replay else protocol["documents"])}
        with ReviewServer(("127.0.0.1", 0), store, demo_replay=replay) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                profile = directory / "chrome"
                process = subprocess.Popen([str(args.chrome), "--headless", "--disable-gpu", "--disable-background-networking",
                    "--disable-sync", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
                    f"--user-data-dir={profile}", "--window-size=1440,1100", "about:blank"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + 15
                while not (profile / "DevToolsActivePort").exists() and time.monotonic() < deadline:
                    time.sleep(.1)
                port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                    target = next(item["id"] for item in json.load(response) if item["type"] == "page")
                client = DevTools(port, target)
                client.call("Page.enable")
                client.call("Page.navigate", {"url": f"{server.origin}/?token={server.token}"})
                client.wait("typeof state !== 'undefined' && state.detail !== null && !$('page-canvas').hidden")
                client.evaluate("$('actor').value='scripted-fictional-demo'")
                if replay:
                    assert client.evaluate("!$('replay-panel').hidden && $('intake-panel').hidden && document.querySelector('.mode').textContent.includes('replay')")
                    assert client.evaluate("$('replay-cases').children.length===4")
                    assert client.evaluate(f"state.detail.document_id === {json.dumps(ids[cases[0]])}")
                    for route in ("/api/upload", "/api/process-one", "/api/demo/seed"):
                        assert client.evaluate(f"post({json.dumps(route)},{{}}).then(()=>false,error=>error.message.includes('Replay uses recorded'))")
                    for index, case in enumerate(cases):
                        client.evaluate(f"$('replay-cases').children[{index}].click()")
                        client.wait(f"state.detail?.document_id === {json.dumps(ids[case])} && !$('page-canvas').hidden")
                    checked.append("offline replay entrypoint and controls")

                def select(case):
                    client.evaluate(f"selectDocument({json.dumps(ids[case])})")
                    client.wait(f"state.detail?.document_id === {json.dumps(ids[case])} && !$('page-canvas').hidden")
                    return store.get(ids[case])

                def capture(caption):
                    data = client.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                    relative = f"frames/{len(frames)+1:02d}.png"
                    (output / relative).parent.mkdir(exist_ok=True)
                    (output / relative).write_bytes(base64.b64decode(data["data"]))
                    frames.append({"path": relative, "caption": caption})

                clean = select(cases[0])
                assert client.evaluate("$('doc-kind').textContent === 'RECORDED OCR RULES · REPLAY' && $('pilot-panel').hidden")
                checked.append("recorded provenance")
                focus(client, '[data-path="fields.invoice_number"]')
                key(client, "ArrowDown", "ArrowDown", 40)
                assert client.evaluate("state.selectedPath==='fields.issue_date' && document.activeElement.dataset.path===state.selectedPath")
                key(client, "ArrowUp", "ArrowUp", 38)
                key(client, "Enter", "Enter", 13)
                assert client.evaluate("document.activeElement.id==='edit-value' && state.selectedPath==='fields.invoice_number'")
                original = clean["record"]["fields"]["invoice_number"]["value"]
                client.evaluate("$('edit-value').select()")
                client.call("Input.insertText", {"text": original + "-DEMO"})
                enter(client, "#save-edit")
                client.wait("state.detail.revision === 2")
                assert store.get(ids[cases[0]])["record"]["fields"]["invoice_number"]["value"] == original + "-DEMO"
                assert store.get(ids[cases[0]], 1)["record"]["fields"]["invoice_number"]["value"] == original
                checked.extend(["keyboard field navigation", "native action buttons", "correction creates revision"])
                assert client.evaluate("(() => {const box=$('page-frame').getBoundingClientRect();return box.bottom>50 && box.top<innerHeight && getComputedStyle(document.querySelector('.page-panel')).position==='sticky'})()")
                checked.append("source stays visible during correction")
                client.evaluate("window.scrollTo(0,0)")
                capture("A keyboard correction creates revision 2 and preserves the original suggestion.")
                enter(client, "#export-json")
                client.wait("$('notice').classList.contains('error')")
                assert store.get(ids[cases[0]])["approval"] is None
                checked.append("unapproved export blocked")
                response = client.evaluate(f"post('/api/documents/{ids[cases[0]]}/edit',{{revision:1,path:'fields.total',value:'0',actor:'scripted-fictional-demo'}}).then(()=>false,error=>error.message)")
                assert response and store.get(ids[cases[0]])["revision"] == 2
                checked.append("stale revision rejected")
                client.evaluate(f"$('edit-value').value={json.dumps(original)}")
                enter(client, "#save-edit")
                client.wait("state.detail.revision===3")
                enter(client, "#approve-button")
                client.wait("state.detail.approval!==null")
                assert store.get(ids[cases[0]])["approval"]["revision"] == 3
                checked.append("approval bound to revision")
                for kind in ("json", "csv"):
                    enter(client, f"#export-{kind}")
                    filename = "invoice.json" if kind == "json" else "header.csv"
                    client.wait(f"$('downloads').querySelector('a')?.textContent === '{filename}'")
                    downloads = client.evaluate("Promise.all([...$('downloads').querySelectorAll('a')].map(async link=>{const response=await fetch(link.href);return {name:link.textContent,ok:response.ok,bytes:Array.from(new Uint8Array(await response.arrayBuffer()))}}))")
                    assert {d["name"] for d in downloads} == ({"invoice.json"} if kind == "json" else {"header.csv", "line-items.csv"})
                    for downloaded in downloads:
                        assert downloaded["ok"]
                        saved, _ = store.exported_file(ids[cases[0]], 3, kind, downloaded["name"])
                        assert bytes(downloaded["bytes"]) == saved
                        if replay and kind == "json":
                            assert json.loads(saved)["extraction"]["profile"] == "replay_ocr_rules"
                checked.append("JSON and CSV downloads")
                capture("Only the approved revision can produce verified JSON and CSV downloads.")
                select(cases[1])
                enter(client, "#approve-button")
                client.wait("$('notice').classList.contains('error')")
                assert store.get(ids[cases[1]])["approval"] is None
                client.evaluate("$('issues').scrollIntoView(); document.querySelector('#issues input').value='Scripted fixture: preserve the printed conflicting total; no business approval claim'; document.querySelector('#issues button').click()")
                client.wait("state.detail.decisions.length===1")
                capture("Printed total conflict: the reviewer records a reason; source values stay unchanged.")
                rotated = select(cases[2])
                client.evaluate("window.scrollTo(0,0)")
                for angle in (0, 90, 180, 270):
                    if angle:
                        enter(client, "#rotate-right")
                    verify_geometry(client, rotated, angle)
                capture("Review rotation: the page and cited line highlights rotate together.")
                checked.append("rotated page pixels and highlights")
                client.evaluate("document.querySelector('#line-items [data-path$=\".tax\"]').click()")
                client.wait("$('highlights').children.length===0")
                assert client.evaluate("$('evidence-note').textContent==='No cited line on this page'")
                checked.append("missing evidence visible")
                multiple = select(cases[3])
                client.evaluate("document.querySelector('[data-path=\"fields.invoice_number\"]').click()")
                client.evaluate("globalThis.pageKeys=[];document.addEventListener('keydown',event=>{if(event.target.id==='page-select')queueMicrotask(()=>pageKeys.push({key:event.key,prevented:event.defaultPrevented,trusted:event.isTrusted}))})")
                focus(client, "#page-select")
                key(client, "ArrowDown", "ArrowDown", 40)
                key(client, "Enter", "Enter", 13)
                events = client.evaluate("pageKeys")
                assert {e["key"] for e in events} == {"ArrowDown", "Enter"}
                assert all(e["trusted"] and not e["prevented"] for e in events)
                assert client.evaluate("state.selectedPath==='fields.invoice_number' && document.activeElement.id==='page-select'")
                client.evaluate("$('page-select').value='2';$('page-select').dispatchEvent(new Event('change',{bubbles:true}))")
                client.wait("state.pageNumber===2 && !$('page-canvas').hidden")
                assert client.evaluate("document.activeElement.id==='page-select'")
                checked.append("native page selector")
                row = next(row for row in multiple["record"]["line_items"] if any(s["id"] in row["line_total"]["evidence_ids"] for s in multiple["pages"][1]["spans"]))
                path = f"line_items.{row['row_id']}.line_total"
                enter(client, "#page-previous")
                client.wait("state.pageNumber===1 && !$('page-canvas').hidden")
                client.evaluate(f"document.querySelector('[data-path=\"{path}\"]').click()")
                client.wait("state.pageNumber===2 && !$('page-canvas').hidden")
                checked.append("field jumps to source page")
                enter(client, "#rotate-right")
                verify_geometry(client, multiple, 90)
                enter(client, "#page-previous")
                client.wait("state.pageNumber===1 && !$('page-canvas').hidden")
                assert client.evaluate("$('rotation-label').textContent==='0°'")
                enter(client, "#page-next")
                client.wait("state.pageNumber===2 && !$('page-canvas').hidden")
                assert client.evaluate("$('rotation-label').textContent==='90°'")
                checked.append("page-specific rotation")
                client.evaluate("window.scrollTo(0,0)")
                capture("A cited row jumps to page 2; each page retains its own review rotation.")
                client.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
                client.wait("document.documentElement.scrollWidth<=window.innerWidth+1")
                checked.append("responsive layout")
                client.call("Emulation.clearDeviceMetricsOverride")
                client.evaluate("window.scrollTo(0,0)")
                assert set(checked) == CHECKS | ({"offline replay entrypoint and controls"} if replay else set())
                print(json.dumps({"status": "controls_passed", "checks": len(checked), "frames": len(frames)}), flush=True)
                if not args.skip_recording:
                    client.call("Page.navigate", {"url": "about:blank"})
                    client.wait("location.href==='about:blank' && document.readyState==='complete'")
                    client.socket.settimeout(60)
                    try:
                        recording = record_demo(client, frames, output)
                    finally:
                        client.socket.settimeout(10)
                assert all((root / name).read_text() == text for name, text in snapshot.items()), "Source changed during verification"
                (output / "index.html").write_text(render_demo(frames, recording))
                report = {"report_version": "portfolio-demo-replay-browser-v1" if replay else "review-browser-workflow-v1", "status": "passed", "checks": checked,
                          "human_timing_measurement": False, "browser": client.call("Browser.getVersion")["product"],
                          "cases": list(cases), "frames": frames, "recording": recording,
                          "page_selector_method": "Trusted CDP arrow/Enter events must remain uncanceled and preserve field selection/focus. Page choice uses a separate DOM change; OS popup-menu navigation is not verified.",
                          "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()},
                          "artifacts": {str(p.relative_to(output)): file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()},
                          "scope": "Automated browser verification on four fictional development cases, replaying verified OCR/rules suggestions. Temporary database, exports, and Chrome profile removed. No human review outcomes, live parsing, model inference, genuine scans, or productivity measurement."}
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"status": "passed", "output": str(output)}), flush=True)
            except Exception as exc:
                report = {"report_version": "review-browser-workflow-v1", "status": "failed", "checks": checked,
                          "human_timing_measurement": False, "failure": f"{type(exc).__name__}: {exc}"}
                if client:
                    try:
                        report["last_ui_state"] = client.evaluate("({revision:state.detail?.revision,selected:state.selectedPath,page:state.pageNumber,page_value:$('page-select').value,focus:document.activeElement.id,notice:$('notice').textContent})")
                    except Exception:
                        pass
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                raise
            finally:
                if client:
                    client.close()
                if process:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    main()
