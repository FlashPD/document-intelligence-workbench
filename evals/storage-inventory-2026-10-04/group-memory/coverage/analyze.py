"""Post-run content-free inspection windows; does not replace frozen acceptance."""
import collections, contextlib, datetime, gzip, hashlib, json, sqlite3, sys
from pathlib import Path

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def seconds(value):
    return datetime.datetime.fromisoformat(value).timestamp()

def analyze(path):
    memory = json.loads((path / 'memory.json').read_text())
    measurements = json.loads((path / 'measurements.json').read_text())
    rows, launches, sources = [], [], {}
    for file in ['memory.json', 'measurements.json', 'report.json', 'group-report.json']:
        sources[file] = sha(path / file)
    for index, entry in enumerate(memory):
        group = entry['native_group']
        artifact = path / group['artifact']
        sources[artifact.name] = sha(artifact)
        samples = json.loads(gzip.decompress(artifact.read_bytes()))
        windows = [(s['native']['start_time']['wall_time_s'] + 978307200,
                    s['native']['end_time']['wall_time_s'] + 978307200) for s in samples]
        # Check epoch conversion against the utility's explicit zoned date.
        assert all(abs(seconds(s['native']['start_time']['date']) - start) < .002
                   for s, (start, _) in zip(samples, windows))
        name = f'cold-{index}' if index < 3 else 'warm'
        db = path / name / 'review.sqlite'
        with contextlib.closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            for row in measurements:
                if (row['id'] == name if index < 3 else not row['id'].startswith('cold-')):
                    events = connection.execute(
                        "SELECT detail,created_at FROM review_events WHERE document_id=? AND kind='processing_stage' ORDER BY id",
                        (row['document_id'],)).fetchall()
                    stages = []
                    for n, (detail, date) in enumerate(events):
                        start = seconds(date)
                        finish = seconds(events[n + 1][1]) if n + 1 < len(events) else row['processing_finished_at_seconds']
                        stage = json.loads(detail)['stage'].lower()
                        assert abs(finish - start - row['stages_seconds'][stage]) < .000001
                        intersect = [max(0., min(finish, end) - max(start, begin)) for begin, end in windows]
                        stages.append({'stage': stage, 'duration_seconds': finish - start,
                                       'inspection_windows_intersecting': sum(v > 0 for v in intersect),
                                       'inspection_window_overlap_seconds': sum(intersect)})
                    rows.append({'id': row['id'], 'launch': name, 'stages': stages,
                                 'processing_started_at_seconds': row['processing_started_at_seconds'],
                                 'processing_finished_at_seconds': row['processing_finished_at_seconds']})
        launches.append({'launch': name, 'samples': len(samples),
                         'first_complete_sample_seconds': samples[0]['elapsed_seconds'],
                         'tail_after_last_complete_sample_seconds': group['observation_seconds'] - samples[-1]['elapsed_seconds'],
                         'query_errors': len(group['errors']),
                         'errors_by_reason': dict(collections.Counter(e['reason'] for e in group['errors'])),
                         'errors_by_category': dict(collections.Counter(e['category'] for e in group['errors']))})
    counts = collections.defaultdict(lambda: {'total': 0, 'with_intersecting_window': 0, 'without_intersecting_window': 0})
    for row in rows:
        for stage in row['stages']:
            count = counts[stage['stage']]
            count['total'] += 1
            count['with_intersecting_window' if stage['inspection_windows_intersecting'] else 'without_intersecting_window'] += 1
    return {'version': 1, 'variant': json.loads((path / 'report.json').read_text())['variant'],
            'source_artifact_sha256': sources, 'launches': launches, 'stage_window_counts': dict(counts),
            'measurements': rows,
            'limits': 'Native inspection windows are not continuous occupancy observations. Intersection does not prove every process/allocation was observed during the stage, and zero intersection does not imply zero memory. Startup predates collector readiness; shutdown continues after application exit. Raw stage events were read from disposable workbenches and cross-checked against hash-bound performance durations. Browser, global OS cache and unattributed kernel/driver memory remain excluded. No peak or full-v1 acceptance is inferred.'}

if __name__ == '__main__':
    path, output = map(Path, sys.argv[1:])
    output.write_text(json.dumps(analyze(path), indent=2, allow_nan=False) + '\n')
