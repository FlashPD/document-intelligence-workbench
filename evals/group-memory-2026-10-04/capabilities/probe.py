import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from memory_accounting import DarwinFootprint
output = Path('artifacts/memory-capabilities-2026-10-04-001')
processes = []
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
        result = subprocess.run(['/usr/bin/vmmap', '-summary', str(pid)], capture_output=True, text=True, timeout=30)
        row['vmmap_returncode'] = result.returncode
        (output / f'vmmap-{pid}.txt').write_text(result.stdout + result.stderr)
        # Record only Docker backing-store associations, not arbitrary open filenames.
        result = subprocess.run(['/usr/sbin/lsof', '-n', '-Fn', '-p', str(pid)], capture_output=True, text=True, timeout=15)
        row['docker_backing_files'] = [value[1:] for value in result.stdout.splitlines() if value.startswith('n') and 'com.docker.docker/' in value and value.endswith(('Docker.raw', 'Docker.qcow2'))]
        row['lsof_returncode'] = result.returncode
        processes.append(row)
result = subprocess.run(['/usr/bin/footprint', '--noCategories', '--sysFootprint', '-p', str(os.getpid())], capture_output=True, text=True, timeout=30)
(output / 'footprint-unprivileged.txt').write_text(result.stdout + result.stderr)
root = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/footprint', '--noCategories', '--sysFootprint', '-p', str(os.getpid())], capture_output=True, text=True, timeout=30)
(output / 'footprint-noninteractive.txt').write_text(root.stdout + root.stderr)
report = {'processes': processes, 'footprint_returncode': result.returncode, 'noninteractive_footprint_returncode': root.returncode, 'scope': 'Read-only capability diagnostic; no workload or whole-memory peak claim.'}
(output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
