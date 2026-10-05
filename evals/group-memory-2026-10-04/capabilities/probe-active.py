import json
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from memory_accounting import DarwinFootprint, GuestObserver
output = Path('artifacts/memory-capabilities-2026-10-04-001')
image = subprocess.check_output(['docker', 'image', 'inspect', 'docwork-parser:v3', '--format', '{{.Id}}'], text=True).strip()
observer = GuestObserver(image, time.monotonic())
processes = []
try:
    observer.start()
    time.sleep(1)
    raw = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,comm='], text=True)
    for line in raw.splitlines():
        pid, parent, rss, name = line.strip().split(None, 3)
        if name.endswith('/com.apple.Virtualization.VirtualMachine') or name.endswith('/com.docker.virtualization'):
            pid = int(pid)
            row = {'pid': pid, 'ppid': int(parent), 'rss_bytes': int(rss) * 1024, 'name': name}
            try:
                row['native'] = DarwinFootprint().read(pid)
            except OSError as error:
                row['native_error'] = {'errno': error.errno}
            for utility, args in [('vmmap', ['/usr/bin/vmmap', '-summary', str(pid)]), ('footprint', ['/usr/bin/footprint', '--noCategories', '--sysFootprint', '-p', str(pid)])]:
                result = subprocess.run(args, capture_output=True, text=True, timeout=30)
                row[utility + '_returncode'] = result.returncode
                (output / f'active-{utility}-{pid}.txt').write_text(result.stdout + result.stderr)
            result = subprocess.run(['/usr/sbin/lsof', '-n', '-Fn', '-p', str(pid)], capture_output=True, text=True, timeout=15)
            row['docker_backing_files'] = [value[1:] for value in result.stdout.splitlines() if value.startswith('n') and 'com.docker.docker/' in value and value.endswith(('Docker.raw', 'Docker.qcow2'))]
            row['lsof_returncode'] = result.returncode
            processes.append(row)
    result = subprocess.run(['/usr/bin/footprint', '--noCategories', '-f', 'bytes', *[str(row['pid']) for row in processes], str(os.getpid())], capture_output=True, text=True, timeout=30)
    (output / 'active-footprint-group.txt').write_text(result.stdout + result.stderr)
finally:
    cleanup = observer.close()
report = {'parser_image_id': image, 'processes': processes, 'group_returncode': result.returncode, 'observer_clean_shutdown': cleanup['clean_shutdown'], 'scope': 'Active-VM read-only capability probe with bounded observer; no workload/peak claim.'}
(output / 'active-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
