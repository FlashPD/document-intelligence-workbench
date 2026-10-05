"""Content-free source applicability review; never a runtime or gate certificate."""
from pathlib import Path
import hashlib,json,sys
root=Path.cwd()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
current={str(p):digest(p) for base in ('src/docwork','ui') for p in sorted(Path(base).rglob('*')) if p.is_file() and p.suffix in ('.py','.js','.html','.css')}
reports={
 'adversarial_model':('evals/adversarial-2026-10-04/model-initial/report.json','source_sha256'),
 'adversarial_browser':('evals/adversarial-2026-10-04/browser/report.json','source_sha256'),
 'access':('evals/access-2026-10-04/http-report.json','source_sha256'),
 'access_refreshed':('evals/adversarial-2026-10-04/access/report.json','source_sha256'),
 'invoice_rules_freeze':('evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/freeze.json','source_sha256'),
 'invoice_model_freeze':('evals/invoice-model-heldout-2026-10-03/freeze.json','source_sha256'),
 'receipt_rules_run':('evals/cord-heldout-2026-10-03/test-rules/run.json','source_sha256'),
 'receipt_model_run':('evals/cord-heldout-2026-10-03/test-model/run.json','source_sha256'),
 'model_workflow':('evals/operations-2026-10-04/model-workflow/report.json','source_sha256'),
 'parser':('evals/storage-inventory-2026-10-04/parser/report.json','source_sha256'),
 'stage_recovery':('evals/storage-inventory-2026-10-04/stage-recovery/report.json','source_sha256'),
 'narrated_demo':('evals/narrated-demo-2026-10-04/final/report.json','source_sha256'),
 'group_freeze':('evals/storage-inventory-2026-10-04/group-memory/freeze/protocol.json','source_sha256'),
}
reviews=[]
for name,(path,key) in reports.items():
 p=Path(path);r=json.loads(p.read_text());declared={('src/docwork/'+k if '/' not in k else k):v for k,v in r[key].items()};recorded={k:v for k,v in declared.items() if k in current}
 changed=[{'path':k,'recorded_sha256':v,'current_sha256':current[k]} for k,v in recorded.items() if current[k]!=v]
 missing=sorted(set(current)-set(recorded))
 reviews.append({'id':name,'path':path,'report_sha256':digest(p),'recorded_status':r.get('status'),
 'recorded_image':r.get('parser_image_id',r.get('parser_image')),'recorded_application_ui_files':len(recorded),
 'matching_application_ui_files':len(recorded)-len(changed),'changed_application_ui_sources':changed,'uncovered_application_ui_sources':missing,
 'scope':'Byte identity of paths declared by this original report only. Does not rerun or verify its runtime schedule, source omission, artifact inventory, image equivalence, or full release applicability.'})
result={'version':1,'current_application_ui_sha256':current,'reviews':reviews,
 'limits':'Source checksums are local applicability evidence, not behavioral equivalence or independent attestation. The original reports are unchanged. Current-guard live model/adversarial/browser refresh and complete gate review remain pending; extraction/scoring/prompt/UI source can remain byte-identical while whole-application inventory or image changes.'}
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps([{k:r[k] for k in ('id','recorded_application_ui_files','matching_application_ui_files','changed_application_ui_sources','uncovered_application_ui_sources')} for r in reviews],indent=2))
