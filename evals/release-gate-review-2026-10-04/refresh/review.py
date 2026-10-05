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
 'checkout':'evals/storage-inventory-2026-10-04/final-checkout/report.json',
 'rules':'evals/storage-inventory-2026-10-04/group-memory/rules/report.json',
 'model':'evals/storage-inventory-2026-10-04/group-memory/model/report.json',
 'model_group':'evals/storage-inventory-2026-10-04/group-memory/model/group-report.json',
 'workflow':'evals/storage-inventory-2026-10-04/model-workflow/report.json',
 'browser_controls':'evals/storage-inventory-2026-10-04/review-browser/report.json',
 'portfolio_audit':'evals/storage-inventory-2026-10-04/portfolio-audit/report.json',
 'rules_group':'evals/storage-inventory-2026-10-04/group-memory/rules/group-report.json',
 'source_review':'evals/storage-inventory-2026-10-04/applicability/report.json',
 'comparison_review':'evals/storage-inventory-2026-10-04/applicability/comparison-review.json',
 'lifecycle':'evals/storage-inventory-2026-10-04/native-refresh/lifecycle/report.json',
 'access':'evals/storage-inventory-2026-10-04/native-refresh/access/report.json',
 'adversarial_model':'evals/storage-inventory-2026-10-04/native-refresh/model/report.json',
 'adversarial_browser':'evals/storage-inventory-2026-10-04/native-refresh/browser/report.json',
 'invoice_rules':'evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/report.json',
 'invoice_model':'evals/invoice-model-heldout-2026-10-03/report.json',
 'receipt_rules':'evals/cord-heldout-2026-10-03/test-rules/report.json',
 'receipt_model':'evals/cord-heldout-2026-10-03/test-model/report.json',
 'operations':'evals/storage-inventory-2026-10-04/native-refresh/operations/report.json',
 'setup':'evals/storage-inventory-2026-10-04/model-setup/report.json',
 'upgrade':'evals/storage-inventory-2026-10-04/upgrade/report.json',
}
evidence={}
for key,name in paths.items():
 p=Path(name);r=json.loads(p.read_text());checks=r.get('checks',[])
 evidence[key]={'path':name,'sha256':digest(p),'declared_status':r.get('status'),
 'declared_checks':[{'id':c.get('id',c.get('name')),'status':c.get('status')} for c in checks] if isinstance(checks,list) and all(isinstance(c,dict) for c in checks) else None,
 'scope':'Original report declaration/index only; no new live or saved verifier is executed by this index.'}
schedule={
 'G01':('defined',['checkout'],['Confirm ADR/contract/guide consistency on requested final source.']),
 'G02':('covered_current_working_source',['lifecycle','checkout','rules'],['Confirm separately requested final commit/identity; current-source lifecycle runs pass after controlled timing.']),
 'G03':('covered_current_working_source',['lifecycle','stages','narration','checkout'],['Confirm separately requested final commit/identity; current-source cancellation/deletion/reprocess checks pass with owned-runtime limits retained.']),
 'G04':('covered_current_working_source',['lifecycle','parser','checkout'],['Confirm current-guard deletion/budget/disk behavior; retain logical deletion and external-copy exclusions.']),
 'G05':('covered_current_working_source',['access','adversarial_browser','narration','source_review','checkout'],['Current-source native authority/access/browser checks pass after timing; confirm requested final commit/identity and trusted-local-operator boundary.']),
 'G06':('partial_missing_assessments',['checkout'],['Complete source-inspected semantic/geometry judgments on every frozen production target in both variants; do not substitute reference existence.']),
 'G07':('covered_working_source',['stages','narration','checkout'],['Confirm relevant final-source/identity and unresolved-issue coverage before closure.']),
 'G08':('covered_current_working_source',['parser','adversarial_model','adversarial_browser','access','source_review','checkout'],['Current-source native model/browser/access checks pass with saved audits; confirm requested final commit/identity and retain failed source-drift/short-lease cases and bounded-case exclusions.']),
 'G09':('covered_working_source',['stages','parser','upgrade','checkout'],['Confirm restore/lifecycle applicability and final identities; preserve controlled parser/short-lease/process-crash limits.']),
 'G10':('covered_historical_comparisons',['invoice_rules','invoice_model','receipt_rules','receipt_model','comparison_review','checkout'],['Confirm frozen relevant-source reporting/deserializer applicability and preserved failures/masks/default; any extraction/scoring change needs new frozen evidence.']),
 'G11':('partial_missing_inputs',['checkout'],['Acquire at least eight permitted English invoice cases, including two documented genuine scanner captures and all four treatments.','Source-label before predictions; freeze and run both production variants.','Complete separate approved-quality assessment without replacing original predictions.']),
 'G12':('partial_memory_acceptance',['rules','rules_group','model','model_group'],['Both corrected schedules complete with source-current verification/cleanup/stage coverage; sampled maxima remain lower bounds.','Resolve unavailable startup/shutdown/short-stage peaks and browser/global-cache/unattributed kernel-driver boundaries; sampled maxima do not close this gap.']),
 'G13':('covered_current_working_source',['operations','checkout'],['Corrected-source authenticated operations checks pass after timing; confirm requested final identity and preserve injected-outcome limits.']),
 'G14':('covered_current_working_source',['setup','workflow','upgrade','parser','source_review','checkout'],['Current-source fresh transfer/offline workflow and prior-version upgrade/restore pass; confirm separately requested release commit/tag and published clone setup.']),
 'G15':('partial_release_unfinished',['narration','browser_controls','portfolio_audit','checkout','source_review'],['Current guides/cards reconcile implemented controls with historical measurements; review every mandatory gate against final source/config/input/runtime/human evidence.','Close G01–G14 and G15 prepublication checks before separately requested publication; verify requested commit and separate published-tag clone/demo afterward to finish G15.']),
}
result={'version':1,'created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'not_release_ready',
 'identity':{'mode':'uncommitted-working-source-review','base_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'contract_sha256':digest(contract)},
 'gates':[{'id':key,'requirement':requirements[key],'review_state':value[0],'evidence':value[1],'remaining':value[2],'final_closure_proven':False} for key,value in schedule.items()],
 'evidence':evidence,'measurements':{'protocol':'evals/storage-inventory-2026-10-04/group-memory/freeze','rules':'terminal_passed','model':'terminal_passed','scope':'Both complete schedules, portable verification and shutdown retained; no active timing process. Sampled maxima do not prove continuous peaks.'},
 'limits':'A content-free reviewer index, not an executable fifteen-gate acceptance test, runtime attestation, human assessment or release certificate. Declared report statuses and selected references cover subsets; broader gate closure is deliberately unproven. Original reports/measurements are unchanged. Later source/evidence changes require a new review identity; do not reinterpret this snapshot as a future release.'}
destination=Path(sys.argv[1])
if destination.exists():raise ValueError('Use a new gate-review output identity')
result['application_ui_sha256']={str(p):digest(p) for p in [*sorted(Path('src/docwork').glob('*.py')),*sorted(Path('ui').glob('*'))] if p.is_file()}
destination.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'gates':len(result['gates']),'indexed_reports':len(evidence)},indent=2))
