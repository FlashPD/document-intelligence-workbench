import json
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from memory_accounting import GuestObserver
out = Path('artifacts/memory-capabilities-2026-10-04-001')
image = subprocess.check_output(['docker', 'image', 'inspect', 'docwork-parser:v3', '--format', '{{.Id}}'], text=True).strip()
observer = GuestObserver(image, time.monotonic())
try:
    observer.start()
    time.sleep(1)
    rows = [line.strip().split(None, 3) for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,comm='], text=True).splitlines()]
    pids = [int(pid) for pid, parent, rss, name in rows if name.endswith(('/com.apple.Virtualization.VirtualMachine', '/com.docker.virtualization', '/com.docker.backend'))] + [os.getpid()]
    args = ['/usr/bin/footprint', '--noCategories', '--wired', '--swapped', '--sysFootprint', '-f', 'bytes', '-j', str(out / 'group-raw.json'), *map(str, pids)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    (out / 'group-raw.txt').write_text(result.stdout + result.stderr)
    print(json.dumps({'returncode': result.returncode, 'pids': pids, 'json_keys': list(json.loads((out / 'group-raw.json').read_text()))}))
finally:
    print(json.dumps({'observer_clean_shutdown': observer.close()['clean_shutdown']}))
