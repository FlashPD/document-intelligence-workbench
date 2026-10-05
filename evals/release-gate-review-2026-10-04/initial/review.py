"""Index the working-source gate review; this is not a completion verifier."""
from pathlib import Path
import hashlib,json,re,subprocess,sys
from datetime import datetime,timezone
root=Path.cwd()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
contract=Path('docs/v1-release-contract.md')
requirements={m.group(1):m.group(2).strip() for m in re.finditer(r'^\| (G\d\d) \| (.*?) \|',contract.read_text(),re.M)}
assert set(requirements)=={f'G{i:02}' for i in range(1,16)}
paths={
 'parser':'evals/storage-inventory-2026-10-04/parser/report.json',
 'stages':'evals/storage-inventory-2026-10-04/stage-recovery/report.json',
 'narration':'evals/narrated-demo-2026-10-04/final/report.json',
 'checkout':'evals/storage-inventory-2026-10-04/checkout/report.json',
 'rules':'evals/storage-inventory-2026-10-04/group-memory/rules/report.json',
 'rules_group':'evals/storage-inventory-2026-10-04/group-memory/rules/group-report.json',
 'source_review':'evals/storage-inventory-2026-10-04/applicability/report.json',
 'comparison_review':'evals/storage-inventory-2026-10-04/applicability/comparison-review.json',
 'lifecycle':'evals/operations-2026-10-04/lifecycle-current-image/report.json',
 'access':'evals/adversarial-2026-10-04/access/report.json',
 'adversarial_model':'evals/adversarial-2026-10-04/model-initial/report.json',
 'adversarial_browser':'evals/adversarial-2026-10-04/browser/report.json',
 'invoice_rules':'evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/report.json',
 'invoice_model':'evals/invoice-model-heldout-2026-10-03/report.json',
 'receipt_rules':'evals/cord-heldout-2026-10-03/test-rules/report.json',
 'receipt_model':'evals/cord-heldout-2026-10-03/test-model/report.json',
 'operations':'evals/operations-2026-10-04/http-report.json',
 'setup':'evals/model-setup-2026-10-04/transfer/report.json',
 'upgrade':'evals/reproducibility-2026-10-04/upgrade/report.json',
}
evidence={}
for key,name in paths.items():
 p=Path(name);r=json.loads(p.read_text());checks=r.get('checks',[])
 evidence[key]={'path':name,'sha256':digest(p),'declared_status':r.get('status'),
 'declared_checks':[{'id':c.get('id',c.get('name')),'status':c.get('status')} for c in checks] if isinstance(checks,list) and all(isinstance(c,dict) for c in checks) else None,
 'scope':'Original report declaration/index only; no new live or saved verifier is executed by this index.'}
schedule={
 'G01':('defined',['checkout'],['Confirm ADR/contract/guide consistency on requested final source.']),
 'G02':('covered_pending_applicability',['lifecycle','checkout','rules'],['Confirm corrected-guard lifecycle/browser runtime applicability after controlled timing.']),
 'G03':('covered_pending_applicability',['lifecycle','stages','narration','checkout'],['Confirm final cancellation/deletion/reprocess applicability; preserve owned-runtime limits.']),
 'G04':('covered_pending_applicability',['lifecycle','parser','checkout'],['Confirm current-guard deletion/budget/disk behavior; retain logical deletion and external-copy exclusions.']),
 'G05':('covered_pending_applicability',['access','adversarial_browser','narration','source_review','checkout'],['Refresh current-guard native authority/access/browser evidence after timing.']),
 'G06':('partial_missing_assessments',['checkout'],['Complete source-inspected semantic/geometry judgments on every frozen production target in both variants; do not substitute reference existence.']),
 'G07':('covered_working_source',['stages','narration','checkout'],['Confirm relevant final-source/identity and unresolved-issue coverage before closure.']),
 'G08':('covered_pending_applicability',['parser','adversarial_model','adversarial_browser','access','source_review','checkout'],['Refresh/review current-guard adversarial model/browser/access applicability after timing; retain prior failures and bounded-case exclusions.']),
 'G09':('covered_working_source',['stages','parser','upgrade','checkout'],['Confirm restore/lifecycle applicability and final identities; preserve controlled parser/short-lease/process-crash limits.']),
 'G10':('covered_historical_comparisons',['invoice_rules','invoice_model','receipt_rules','receipt_model','comparison_review','checkout'],['Confirm frozen relevant-source reporting/deserializer applicability and preserved failures/masks/default; any extraction/scoring change needs new frozen evidence.']),
 'G11':('partial_missing_inputs',['checkout'],['Acquire at least eight permitted English invoice cases, including two documented genuine scanner captures and all four treatments.','Source-label before predictions; freeze and run both production variants.','Complete separate approved-quality assessment without replacing original predictions.']),
 'G12':('partial_active_model_and_memory',['rules','rules_group'],['Finish the active fourteen-upload controlled model run and retain verification/cleanup/stage coverage.','Resolve unavailable startup/shutdown/short-stage peaks and browser/global-cache/unattributed kernel-driver boundaries; sampled maxima do not close this gap.']),
 'G13':('covered_pending_applicability',['operations','checkout'],['Confirm corrected-guard authenticated readiness/metrics and distinct model availability after timing.']),
 'G14':('covered_pending_applicability',['setup','upgrade','parser','source_review','checkout'],['Confirm current-guard fresh-source setup/offline workflow and upgrade/restore applicability; later confirm requested release/tag setup.']),
 'G15':('partial_release_unfinished',['narration','checkout','source_review'],['Reconcile guides/cards and close every mandatory gate against applicable source/config/input/runtime/human evidence.','After separately requested commit/publication, verify exact committed source and separate published-tag clone/demo.']),
}
result={'version':1,'created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'not_release_ready',
 'identity':{'mode':'uncommitted-working-source-review','base_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'contract_sha256':digest(contract)},
 'gates':[{'id':key,'requirement':requirements[key],'review_state':value[0],'evidence':value[1],'remaining':value[2],'final_closure_proven':False} for key,value in schedule.items()],
 'evidence':evidence,'active_measurement':{'protocol':'artifacts/group-memory-freeze-2026-10-04-003','output':'artifacts/group-memory-model-2026-10-04-002','exec_session_id':64328,'scope':'Confirmed live when this review was prepared; not a terminal report or proof of continued liveness.'},
 'limits':'A content-free reviewer index, not an executable fifteen-gate acceptance test, runtime attestation, human assessment or release certificate. Declared report statuses and selected references cover subsets; broader gate closure is deliberately unproven. Original reports/measurements are unchanged. Later source/evidence changes require a new review identity; do not reinterpret this snapshot as a future release.'}
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'gates':len(result['gates']),'indexed_reports':len(evidence)},indent=2))
