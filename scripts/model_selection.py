"""Compare exactly two previously fitted models. Never fit or inspect test outcomes."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .audit_raw import ROOT, sha256
from .ranking_evaluation import paired_bootstrap, rank_scores
from .ranking_features import feature_matrix, load_schema
from .ranking_models import load_artifact, score_model
from .release_bundle import dependency_versions, score_values

CONFIG = ROOT/'config/release_v1.json'


def hardware():
    cpu = next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')),'unknown')
    return {'platform':platform.platform(),'python':platform.python_version(),'cpu':cpu,
        'logical_cpus':os.cpu_count(),'affinity_cpus':len(os.sched_getaffinity(0)),
        'cpu_quota':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
        'memory_limit_bytes':Path('/sys/fs/cgroup/memory.max').read_text().strip()}


def percentiles(values):
    return {'count':len(values),'p50_ms':float(np.percentile(values,50)),
            'p95_ms':float(np.percentile(values,95)),'mean_ms':float(np.mean(values))}


def select_model(report):
    policy = json.loads(CONFIG.read_text())['selection']
    difference = report['paired_ndcg_difference_xgb_minus_logistic']
    recall_loss = report['quality']['xgb_depth5']['recall_at_10']-report['quality']['logistic']['recall_at_10']
    size_ratio = report['operations']['xgb_depth5']['artifact_bytes']/report['operations']['logistic']['artifact_bytes']
    prefer_simple = (abs(difference['mean_difference']) <= policy['negligible_observed_ndcg_difference']
        and difference['lower'] <= 0 <= difference['upper']
        and recall_loss <= policy['maximum_recall_sacrifice']
        and size_ratio >= policy['meaningful_artifact_size_ratio'])
    return 'logistic' if prefer_simple else 'xgb_depth5'


def compare_models(milestone2=ROOT/'outputs/milestone2',output=ROOT/'outputs/milestone3'):
    milestone2,output = Path(milestone2),Path(output)
    output.mkdir(parents=True,exist_ok=True)
    config = json.loads(CONFIG.read_text())
    metrics_path = milestone2/'validation_query_metrics.parquet'
    features_path = milestone2/'validation_features.parquet'
    manifest = json.loads((milestone2/'run_manifest.json').read_text())
    for relative in ['validation_query_metrics.parquet','validation_features.parquet',
                     'models/logistic.joblib','models/xgb_depth5.joblib']:
        if sha256(milestone2/relative) != manifest['artifacts'][relative]['sha256']:
            raise ValueError('Existing Milestone 2 artifact changed: '+relative)
    metrics = pd.read_parquet(metrics_path)
    if not metrics.cutoff.between(pd.Timestamp('2011-02-01'),pd.Timestamp('2011-04-01')).all():
        raise ValueError('Only existing validation metrics are permitted')
    schema = load_schema()
    features = pd.read_parquet(features_path,columns=['query_id','sku']+schema['features'],
                               filters=[('cutoff','=',pd.Timestamp('2011-02-01'))])
    groups = dict(tuple(features.groupby('query_id',sort=True)))
    rng = np.random.default_rng(config['benchmark']['seed'])
    query_ids = rng.choice(sorted(groups),config['benchmark']['warm_queries'],replace=False)
    frames = [groups[query].reset_index(drop=True) for query in query_ids]
    if any(len(frame) != 200 for frame in frames):
        raise ValueError('Operational benchmark requires realistic full 200-candidate queries')
    matrices = [feature_matrix(frame,schema) for frame in frames]
    operations,quality,inputs = {},{}, {'validation_query_metrics':sha256(metrics_path),
        'validation_features':sha256(features_path),'release_config':sha256(CONFIG)}
    threads = config['benchmark']['native_threads']
    with threadpool_limits(limits=threads):
        for name in ['logistic','xgb_depth5']:
            path = milestone2/'models'/(name+'.joblib')
            inputs[name] = sha256(path)
            load_times = []
            for _ in range(config['benchmark']['load_repetitions']):
                started = time.perf_counter()
                artifact = load_artifact(path)
                load_times.append(1000*(time.perf_counter()-started))
            for matrix in matrices[:config['benchmark']['warmup_queries']]:
                score_values(artifact,matrix)
            warm_times,frozen_times = [],[]
            for frame,matrix in zip(frames,matrices):
                started = time.perf_counter()
                scores = score_values(artifact,matrix)
                rank_scores(frame.sku,scores,k=10)
                warm_times.append(1000*(time.perf_counter()-started))
                started = time.perf_counter()
                frozen = score_model(artifact,frame)
                rank_scores(frame.sku,frozen,k=10)
                frozen_times.append(1000*(time.perf_counter()-started))
                np.testing.assert_allclose(scores,frozen,atol=1e-12,rtol=1e-12)
            cold_times = []
            for _ in range(3):
                started = time.perf_counter()
                subprocess.run([sys.executable,'-c',
                    'from scripts.ranking_models import load_artifact; import sys; load_artifact(sys.argv[1])',str(path)],
                    cwd=ROOT,check=True,capture_output=True)
                cold_times.append(1000*(time.perf_counter()-started))
            operations[name] = {'artifact_bytes':path.stat().st_size,'warm_artifact_load':percentiles(load_times),
                'cold_python_import_and_load':percentiles(cold_times),'stored_matrix_score_and_top10':percentiles(warm_times),
                'frozen_dataframe_score_and_top10':percentiles(frozen_times),'pipeline_equivalence_checked_queries':len(frames)}
            quality[name] = {key:float(metrics[name+'__'+key].mean()) for key in
                ['ndcg_at_10','recall_at_10','repeat_recall_at_10','discovery_recall_at_10']}
    interval = paired_bootstrap(metrics,'xgb_depth5__ndcg_at_10','logistic__ndcg_at_10',
        replicates=config['selection']['bootstrap_replicates'],seed=config['selection']['seed'])
    report = {'quality':quality,'paired_ndcg_difference_xgb_minus_logistic':interval,
        'operations':operations,'conditions':{'hardware':hardware(),'dependencies':dependency_versions(),
            'benchmark':config['benchmark'],'candidate_rows_per_query':200,'k':10,
            'sample':'500 seeded distinct February validation queries; no outcomes used for timing selection',
            'warm_load':'Already imported dependencies and warm OS file cache',
            'cold_load':'Fresh Python subprocess, imports plus artifact load plus interpreter overhead; warm file cache',
            'warm_score':'Single caller; fixed 4 native threads; fitted preprocessing, scoring and lexical-tie top10 ranking included. Stored-matrix path excludes one-time feature extraction; dataframe path includes frozen per-call extraction/thread configuration.',
            'memory':'API startup and process memory measured separately in the local load report'},
        'selection_policy':config['selection'],'input_sha256':inputs,'test_evaluated':False,'models_retrained':False,
        'validation_primary_queries':int(metrics.logistic__ndcg_at_10.notna().sum())}
    report['selected_model'] = select_model(report)
    report['decision'] = ('The observed NDCG and recall advantages of XGBoost are small and the paired interval includes zero. Logistic is materially smaller and simpler to operate. Prefer logistic for this local release; the interval does not prove quality equivalence and allows a modest XGBoost advantage.'
        if report['selected_model'] == 'logistic' else 'The predefined simplicity criteria were not all met; retain xgb_depth5. No additional tuning was performed.')
    (output/'model_comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    lines = ['# Saved-model selection', '', report['decision'], '',
        '| Model | NDCG@10 | Recall@10 | Repeat recall | Discovery recall | Artifact bytes | Warm score+rank p50/p95 ms | Warm load p50 ms |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |']
    for name in quality:
        q,o = quality[name],operations[name]
        lines.append('| '+name+' | '+' | '.join(f'{q[key]:.6f}' for key in q)+' | '+str(o['artifact_bytes'])+
            f" | {o['stored_matrix_score_and_top10']['p50_ms']:.3f}/{o['stored_matrix_score_and_top10']['p95_ms']:.3f} | {o['warm_artifact_load']['p50_ms']:.3f} |")
    lines += ['', 'XGBoost minus logistic customer-cluster paired bootstrap:', '```json',json.dumps(interval,indent=2),'```',
              '', 'Measurement conditions:', '```json',json.dumps(report['conditions'],indent=2),'```']
    (output/'model_comparison.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'selected_model':report['selected_model'],'interval':interval,'operations':operations},indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--milestone2',type=Path,default=ROOT/'outputs/milestone2')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone3')
    args = parser.parse_args()
    compare_models(args.milestone2,args.output)
