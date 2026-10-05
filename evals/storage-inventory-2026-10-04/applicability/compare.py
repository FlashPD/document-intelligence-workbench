"""Inspect historical comparison source boundaries without running extraction."""
import ast,hashlib,json,sys
from pathlib import Path

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def nodes(text):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
model=Path('evals/invoice-model-heldout-2026-10-03')
baseline=Path('evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1')
current=Path('src/docwork/review.py')
a=nodes(json.loads((model/'source_snapshot.json').read_text())['review.py']);b=nodes(current.read_text())
names=['_now','page_from_dict','record_from_dict'];assert all(a[n]==b[n] for n in names)
assert Path('src/docwork/heldout.py').read_bytes()==(baseline/'reporting_source_snapshot.py').read_bytes()
r={'version':1,'model_snapshot_sha256':sha(model/'source_snapshot.json'),'current_review_sha256':sha(current),
 'top_level_review_ast_changes':[n for n in sorted(set(a)|set(b)) if a.get(n)!=b.get(n)],
 'relevant_functions':{n:{'recorded_ast_sha256':hashlib.sha256(a[n].encode()).hexdigest(),'current_ast_sha256':hashlib.sha256(b[n].encode()).hexdigest(),'identical':a[n]==b[n]} for n in names},
 'model_runner_sha256':sha(Path('src/docwork/invoice_model_run.py')),
 'model_runner_inspection':'invoice_model_run.py imports _now and page_from_dict from review and does not instantiate ReviewStore. This call-site conclusion is manually inspected, not proven by the AST assertions.',
 'baseline_original_driver_sha256':json.loads((baseline/'freeze.json').read_text())['source_sha256']['heldout.py'],
 'baseline_current_driver_sha256':sha(Path('src/docwork/heldout.py')),
 'baseline_reporting_snapshot_sha256':sha(baseline/'reporting_source_snapshot.py'),'baseline_reporting_snapshot_current':True,
 'limits':'Static inspection of declared imported functions and stored reporting correction. Whole original review-module/freeze inventory is not current. No new OCR/model predictions or fresh comparison is inferred; original reports/predictions remain unchanged. Checkout separately rescored saved evidence.'}
Path(sys.argv[1]).write_text(json.dumps(r,indent=2)+'\n')
