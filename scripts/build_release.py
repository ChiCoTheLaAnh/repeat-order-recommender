"""Build a past-only historical serving release from the existing M2 artifacts."""
import argparse
import json
from pathlib import Path
import platform
import subprocess

import pandas as pd

from .audit_raw import ROOT, sha256
from .milestone2 import validation_candidates
from .model_selection import compare_models, select_model
from .ranking_features import generate_features, load_schema
from .release_bundle import create_bundle, ReleaseBundle
from .retrieval import RetrievalIndex, load_policy
from .temporal_snapshots import REQUIRED_COLUMNS, build_snapshot, recommendation_eligibility


def item_metadata(paid,cutoff):
    past = paid.loc[paid.invoice_date.lt(cutoff)]
    past = past.loc[recommendation_eligibility(past).recommendation_eligible_v1].copy()
    last = past.groupby('sku',sort=True).invoice_date.max().rename('last_observed_at')
    past['description'] = past.description.astype('string').str.strip().replace('',pd.NA)
    descriptions = (past.loc[past.description.notna()].sort_values(['invoice_date','record_id'],kind='stable')
                    .drop_duplicates('sku',keep='last').set_index('sku').description)
    return last.to_frame().join(descriptions).reset_index()[['sku','description','last_observed_at']]


def build_release(milestone2=ROOT/'outputs/milestone2',destination=ROOT/'outputs/releases',
                  comparison_path=ROOT/'outputs/milestone3/model_comparison.json',require_clean=False):
    milestone2,destination,comparison_path = Path(milestone2),Path(destination),Path(comparison_path)
    config = json.loads((ROOT/'config/release_v1.json').read_text())
    cutoff = pd.Timestamp(config['serving_cutoff'])
    if require_clean:
        from .cloud_deploy import clean_commit
        clean_commit()
    if cutoff != pd.Timestamp('2011-05-01'):
        raise ValueError('Release V1 stops at the validation boundary; no test-era purchases may be loaded')
    m2_path = milestone2/'run_manifest.json'
    m2 = json.loads(m2_path.read_text())
    if m2['status'] != 'complete' or m2['data_access']['test_rows_loaded'] != 0:
        raise ValueError('Expected completed train/validation-only M2 artifacts')
    for name,digest in m2['protected_and_code_sha256'].items():
        source = ROOT/Path(name).parent.name/Path(name).name
        if sha256(source) != digest:
            raise ValueError('Frozen code or configuration changed: '+source.name)
    for name in ['logistic','xgb_depth5']:
        for extension in ['joblib','metadata.json']:
            relative = 'models/'+name+'.'+extension
            if sha256(milestone2/relative) != m2['artifacts'][relative]['sha256']:
                raise ValueError('Previously trained model artifact or metadata changed')
    if not comparison_path.exists():
        if require_clean:
            raise ValueError('Deployable builds reuse the saved logistic selection; provide existing M3 evidence')
        compare_models(milestone2,comparison_path.parent)
    comparison = json.loads(comparison_path.read_text())
    expected_inputs = {'logistic':milestone2/'models/logistic.joblib',
        'xgb_depth5':milestone2/'models/xgb_depth5.joblib',
        'validation_query_metrics':milestone2/'validation_query_metrics.parquet',
        'validation_features':milestone2/'validation_features.parquet','release_config':ROOT/'config/release_v1.json'}
    if any(comparison['input_sha256'][key] != sha256(path) for key,path in expected_inputs.items()):
        raise ValueError('Selection report is stale or belongs to different artifacts/configuration')
    selected = select_model(comparison)
    if comparison['selected_model'] != selected or comparison['test_evaluated'] or comparison['models_retrained']:
        raise ValueError('Invalid model-selection evidence')
    if require_clean and selected!='logistic':
        raise ValueError('Cloud demo preserves the selected logistic artifact')
    for name in ['queries.parquet','labels.parquet']:
        if sha256(ROOT/'outputs/snapshots'/name)!=m2['inputs_sha256']['snapshots'][name]:
            raise ValueError('Frozen snapshot data changed: '+name)
    train_windows=pd.read_parquet(ROOT/'outputs/snapshots/queries.parquet',
        columns=['cutoff','target_end'],filters=[('split','=','train')]).drop_duplicates().sort_values('cutoff')
    train_labels=pd.read_parquet(ROOT/'outputs/snapshots/labels.parquet',
        columns=['cutoff','target_end'],filters=[('split','=','train')])
    if (not train_windows.target_end.le(cutoff).all() or not train_labels.target_end.le(cutoff).all()
        or not train_windows.target_end.eq(train_windows.cutoff+pd.offsets.MonthBegin(1)).all()):
        raise ValueError('Training label windows exceed serving data_as_of or changed their definition')
    trained=json.loads((milestone2/'models/logistic.metadata.json').read_text())['training']['cutoffs']
    if list(train_windows.cutoff.dt.strftime('%Y-%m-%d'))!=trained:
        raise ValueError('Training-window proof differs from saved model training cutoffs')
    paid_path = ROOT/'outputs/cleaning/paid_purchases.parquet'
    input_hash = sha256(paid_path)
    if input_hash != m2['inputs_sha256']['cleaning']['paid_purchases.parquet']:
        raise ValueError('Frozen cleaning input differs from the Milestone 2 data')
    paid = pd.read_parquet(paid_path,columns=REQUIRED_COLUMNS+['quantity','description'],
                           filters=[('invoice_date','<',cutoff)])
    if not paid.invoice_date.lt(cutoff).all():
        raise ValueError('Future paid records loaded')
    queries,labels,catalog,snapshot = build_snapshot(paid,cutoff,'serving',observation_end=cutoff)
    if len(labels) or snapshot['labels_mature']:
        raise ValueError('Serving build must never construct target labels')
    print(f'Building {len(queries)} serving queries at {cutoff.date()}, without test snapshots or labels',flush=True)
    policy = load_policy()
    candidates = validation_candidates(paid,queries,catalog,policy)
    features = generate_features(paid,queries,candidates,load_schema())
    items = item_metadata(paid,cutoff)
    index = RetrievalIndex(paid,cutoff,catalog.sku,policy)
    fallback_skus = sorted(catalog.sku,key=lambda sku:(-index.recent_popularity.get(sku,0),sku))[:20]
    fallback = pd.DataFrame({'sku':fallback_skus,'ranking_score':[float(index.recent_popularity.get(sku,0)) for sku in fallback_skus]})
    provenance = {'data_as_of':cutoff.isoformat(),'data_as_of_semantics':'exclusive, timezone-naive workbook event time',
        'model_training_prediction_cutoffs':['2010-03-01','2011-01-01'],
        'model_last_training_feature_cutoff':'2011-01-01T00:00:00',
        'model_training_label_interval':['2010-03-01T00:00:00','2011-02-01T00:00:00'],
        'model_labels_end_exclusive':'2011-02-01T00:00:00',
        'training_label_windows':[{'cutoff':t.isoformat(),'end_exclusive':end.isoformat()}
            for t,end in train_windows.itertuples(index=False,name=None)],
        'validation_prediction_cutoffs':['2011-02-01','2011-04-01'],
        'validation_label_interval':['2011-02-01T00:00:00','2011-05-01T00:00:00'],
        'validation_labels_end_exclusive':'2011-05-01T00:00:00',
        'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'code_worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        'deployable_build':require_clean,
        'code_provenance':'Exact HEAD plus bundled source/config hashes. Deployable builds require a clean committed revision.',
        'python_version':platform.python_version(),
        'input_sha256':{'paid_purchases':input_hash,'milestone2_manifest':sha256(m2_path),'model_comparison':sha256(comparison_path)},
        'data_access':{'paid_upper_bound_exclusive':cutoff.isoformat(),'paid_rows_loaded':len(paid),
                       'test_snapshot_rows_loaded':0,'test_labels_loaded':0,'test_evaluated':False},
        'snapshot':snapshot,'evaluation_summary':{'selected_model':selected,'quality':comparison['quality'],
            'paired_ndcg_difference_xgb_minus_logistic':comparison['paired_ndcg_difference_xgb_minus_logistic'],
            'decision':comparison['decision']},
        'display_policy':'Latest nonempty description on eligible paid purchases strictly before cutoff; last observation is also strictly past. Historical descriptions are not current stock/availability guarantees.',
        'fallback_policy':'Known historical catalog sorted by distinct identified purchasers in [cutoff-30 days, cutoff), then lexical SKU; zero-count catalog items backfill if needed. Anonymous purchases supply catalog only.'}
    code_paths = sorted((ROOT/'scripts').glob('*.py')) + [ROOT/'pyproject.toml',ROOT/'uv.lock']
    for name in ['Dockerfile','.dockerignore','Dockerfile.cloud','config/cloud_demo_v1.json','config/logistic_artifact_v1.json']:
        if (ROOT/name).exists():
            code_paths.append(ROOT/name)
    if require_clean:
        from .cloud_deploy import clean_commit
        if clean_commit()!=provenance['code_commit']:
            raise ValueError('Source revision changed during deployment build')
    final = create_bundle(destination,milestone2/'models'/(selected+'.joblib'),features,items,fallback,provenance,
        code_paths=code_paths,extra_paths={'model_comparison.json':comparison_path,
            'model.metadata.json':milestone2/'models'/(selected+'.metadata.json')})
    if sha256(paid_path) != input_hash:
        raise ValueError('Cleaning input changed during release build')
    loaded = ReleaseBundle.load(final)
    (destination/'latest.json').write_text(json.dumps({'release_id':loaded.manifest['release_id']})+'\n')
    print(json.dumps({'bundle':str(final),'model':selected,'customers':len(loaded.index),
                      'candidates':len(features),'catalog_items':len(items)},indent=2))
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--milestone2',type=Path,default=ROOT/'outputs/milestone2')
    parser.add_argument('--destination',type=Path,default=ROOT/'outputs/releases')
    parser.add_argument('--comparison',type=Path,default=ROOT/'outputs/milestone3/model_comparison.json')
    parser.add_argument('--deployable',action='store_true',help='Require a clean committed checkout and preserve saved logistic selection')
    args = parser.parse_args()
    build_release(args.milestone2,args.destination,args.comparison,args.deployable)
