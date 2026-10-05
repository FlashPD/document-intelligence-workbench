import hashlib
import json
import mmap
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from group_memory import validate_native
from memory_accounting import DarwinFootprint
out=Path('artifacts/native-group-preflight-2026-10-04-002')
size=64*1024**2
code='''import mmap, sys
from pathlib import Path
print("baseline", flush=True)
sys.stdin.readline()
with Path(sys.argv[1]).open("r+b") as file:
    with mmap.mmap(file.fileno(), 0) as memory:
        for offset in range(0, len(memory), 4096): memory[offset]=42
        print("mapped", flush=True)
        sys.stdin.readline()
'''
def capture(name, pids):
    selection=[{'pid':pid, 'start_abstime':DarwinFootprint().read(pid)['start_abstime'], 'component':'application'} for pid in pids]
    path=out/(name+'.json')
    result=subprocess.run(['/usr/bin/footprint','--wired','--swapped','-f','bytes','-j',str(path),*map(str,pids)],capture_output=True,text=True,timeout=15)
    (out/(name+'.txt')).write_text(result.stdout+result.stderr)
    assert result.returncode==0
    data=json.loads(path.read_text())
    values=validate_native(data,selection)
    return values['dirty']-values['swapped']+values['clean']+values['reclaimable']
def ready(process, expected):
    assert select.select([process.stdout],[],[],10)[0], 'child startup timed out'
    assert process.stdout.readline().strip()==expected
report={'status':'failed','method':'native-group-shared-mapping-preflight-v1','allocation_bytes':size}
try:
    with tempfile.TemporaryDirectory(prefix='docwork-shared-mapping-') as temporary:
        path=Path(temporary)/'shared.bin'
        with path.open('wb') as file: file.truncate(size)
        process=subprocess.Popen([sys.executable,'-u','-c',code,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready(process,'baseline')
            baseline=capture('baseline-group',[os.getpid(),process.pid])
            process.stdin.write('map\n');process.stdin.flush();ready(process,'mapped')
            with path.open('r+b') as file:
                with mmap.mmap(file.fileno(),0) as memory:
                    assert sum(memory[offset] for offset in range(0,size,4096))==42*(size//4096)
                    parent=capture('mapped-parent',[os.getpid()])
                    child=capture('mapped-child',[process.pid])
                    together=capture('mapped-group',[os.getpid(),process.pid])
                    report.update(baseline_accounted_resident_bytes=baseline,parent_accounted_resident_bytes=parent,child_accounted_resident_bytes=child,group_accounted_resident_bytes=together,shared_mapping_resident_delta_bytes=together-baseline,independent_sum_less_group_bytes=parent+child-together)
                    assert together-baseline >= size*.8, 'mapping not present in group observations'
                    assert parent+child-together >= size*.8, 'shared mapping not de-duplicated'
            process.stdin.write('quit\n');process.stdin.flush()
            assert process.wait(timeout=10)==0
            report['status']='passed'
        finally:
            if process.poll() is None: process.kill();process.wait(timeout=5)
            process.stdin.close();process.stdout.close();process.stderr.close()
    report['temporary_mapping_removed']=not path.exists()
except Exception as error:
    report['failure']=str(error)
report['source_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__),Path('scripts/group_memory.py'),Path('scripts/memory_accounting.py')]}
report['utility_sha256']=hashlib.sha256(Path('/usr/bin/footprint').read_bytes()).hexdigest()
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
sys.exit(0 if report['status']=='passed' else 2)
