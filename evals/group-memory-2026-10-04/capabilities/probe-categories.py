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
    deadline = time.monotonic() + 15
    while not observer.samples and time.monotonic() < deadline:
        time.sleep(.1)
    assert observer.samples, 'observer startup did not produce counters'
    rows = [line.strip().split(None, 3) for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,comm='], text=True).splitlines()]
    pids = [int(pid) for pid, parent, rss, name in rows if name.endswith(('/com.apple.Virtualization.VirtualMachine', '/com.docker.virtualization', '/com.docker.backend'))] + [os.getpid()]
    args = ['/usr/bin/footprint', '--wired', '--swapped', '--sysFootprint', '-f', 'bytes', '-j', str(out / 'categories-raw.json'), *map(str, pids)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    (out / 'categories-raw.txt').write_text(result.stdout + result.stderr)
    data=json.loads((out / 'categories-raw.json').read_text())
    print(json.dumps({'returncode': result.returncode, 'process_keys': list(data['processes'][0]), 'summary_keys': list(data['summary']), 'shared_type': str(type(data['shared'])), 'errors': data['errors'], 'warnings': data['warnings']}))
finally:
    cleanup = observer.close()
    (out / 'categories-observer-cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    print(json.dumps({'observer_clean_shutdown': cleanup['clean_shutdown'], 'samples': len(cleanup['samples']), 'errors': cleanup['sampling_errors'], 'early_exit': cleanup['early_exit'], 'cleanup_failure': cleanup['cleanup_failure']}))
