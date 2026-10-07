"""Content-addressed local releases; validate every payload before unpickling."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .audit_raw import ROOT, sha256
from .ranking_evaluation import rank_scores
from .ranking_features import feature_matrix, load_schema
from .ranking_models import load_artifact

VERSION = 'release_bundle_v1'
POLICIES = ['ranking_features_v1.json', 'ranking_models_v1.json', 'retrieval_policy_v1.json',
            'stock_code_policy.json', 'release_v1.json']


class BundleValidationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def release_id(manifest):
    return 'release_v1_' + hashlib.sha256(canonical({k:v for k,v in manifest.items() if k != 'release_id'})).hexdigest()


def dependency_versions():
    packages = ['numpy','pandas','pyarrow','scipy','scikit-learn','xgboost-cpu','joblib',
                'threadpoolctl','fastapi','starlette','pydantic','uvicorn','httpx']
    return {name: importlib.metadata.version(name) for name in packages}


def policy_versions():
    read = lambda name: json.loads((ROOT/'config'/name).read_text())
    return {'stock_code_classification':read('stock_code_policy.json')['policy_version'],
            'recommendation':'recommendation_v1',
            'retrieval':read('retrieval_policy_v1.json')['policy_version'],
            'features':read('ranking_features_v1.json')['version'],
            'model_configuration':read('ranking_models_v1.json')['version'],
            'release':VERSION}


def score_values(artifact, values):
    """Same fitted pipeline and raw margins as M2, with process-wide thread limits."""
    pipeline = artifact['pipeline']
    scores = (pipeline.decision_function(values) if artifact['name'] == 'logistic'
              else pipeline.predict(values, output_margin=True))
    scores = np.asarray(scores, dtype=np.float64)
    if scores.shape != (len(values),) or not np.isfinite(scores).all():
        raise BundleValidationError('Nonfinite or malformed model scores')
    return scores


def create_bundle(destination, model_path, features, items, fallback, provenance,
                  policy_paths=None, code_paths=(), extra_paths=None):
    """Never rewrite an existing release. Identical inputs resolve to the same directory."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    policy_paths = list(policy_paths or [ROOT/'config'/name for name in POLICIES])
    schema = load_schema()
    features = features[['cutoff','query_id','customer_id','sku','selected_source'] + schema['features']].copy()
    features = features.sort_values(['customer_id','candidate_rank','sku'], kind='stable').reset_index(drop=True)
    customers = {}
    for customer, group in features.groupby('customer_id',sort=False):
        customers[str(customer)] = [int(group.index[0]),int(group.index[-1]+1)]
    with tempfile.TemporaryDirectory(prefix='.staging-',dir=destination) as temporary:
        stage = Path(temporary)
        shutil.copyfile(model_path,stage/'model.joblib')
        features.to_parquet(stage/'candidate_features.parquet',index=False,compression='zstd')
        items.sort_values('sku').to_parquet(stage/'items.parquet',index=False,compression='zstd')
        fallback.to_parquet(stage/'fallback.parquet',index=False,compression='zstd')
        (stage/'customer_index.json').write_bytes(canonical(customers))
        for source in policy_paths:
            target = stage/'policies'/source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source,target)
        for source in code_paths:
            source = Path(source)
            target = stage/'code'/source.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        for name, source in (extra_paths or {}).items():
            target = stage/name
            if target.is_absolute() and not target.is_relative_to(stage):
                raise BundleValidationError('Unsafe bundle payload path')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        artifact = load_artifact(model_path)
        manifest = {'bundle_version': VERSION,'model_name':artifact['name'],
            'model_version':artifact['name']+'_'+sha256(model_path)[:16],
            'data_as_of':provenance['data_as_of'], 'history_interval':'invoice_date < data_as_of (exclusive)',
            'feature_schema_version':schema['version'],'feature_names':schema['features'],
            'policy_versions':policy_versions(),
            'provenance':provenance,'dependency_versions':dependency_versions(),
            'customers':len(customers),'candidate_rows':len(features),'catalog_items':len(items),
            'max_candidates':200,'max_k':20,'native_threads':artifact['threads'],
            'scores':'Raw model margins for personalized mode; distinct 30-day identified purchasers for fallback. Neither is a calibrated purchase probability.',
            'files':{str(path.relative_to(stage)):{'sha256':sha256(path),'bytes':path.stat().st_size}
                     for path in sorted(stage.rglob('*')) if path.is_file()}}
        manifest['release_id'] = release_id(manifest)
        (stage/'manifest.json').write_bytes(canonical(manifest))
        ReleaseBundle.load(stage)  # All semantic and integrity checks before publication.
        # Temporary directories start at 0700. Publish readable by the Docker UID,
        # with deterministic read-only permissions rather than preserving umask.
        for payload in stage.rglob('*'):
            payload.chmod(0o555 if payload.is_dir() else 0o444)
        stage.chmod(0o555)
        final = destination/manifest['release_id']
        if final.exists():
            ReleaseBundle.load(final)
            if (final/'manifest.json').read_bytes() != (stage/'manifest.json').read_bytes():
                raise BundleValidationError('Immutable release collision')
        else:
            stage.rename(final)
    return final


class ReleaseBundle:
    @classmethod
    def load(cls, path):
        try:
            return cls(Path(path))
        except BundleValidationError:
            raise
        except Exception as error:
            raise BundleValidationError('Bundle validation failed: '+type(error).__name__) from error

    def __init__(self,path):
        self.path = path
        self.manifest = manifest = json.loads((path/'manifest.json').read_text())
        if manifest.get('bundle_version') != VERSION:
            raise BundleValidationError('Unsupported bundle version')
        schema = load_schema()
        if manifest.get('feature_names') != schema['features'] or manifest.get('feature_schema_version') != schema['version']:
            raise BundleValidationError('Ordered feature schema mismatch')
        if manifest['release_id'] != release_id(manifest):
            raise BundleValidationError('Manifest integrity hash mismatch')
        required = {'model.joblib','candidate_features.parquet','customer_index.json','items.parquet','fallback.parquet'}
        required |= {'policies/'+name for name in POLICIES}
        if not required.issubset(manifest['files']):
            raise BundleValidationError('Missing required bundle payload')
        for name, expected in manifest['files'].items():
            relative = Path(name)
            file = path/relative
            if relative.is_absolute() or '..' in relative.parts or not file.resolve().is_relative_to(path.resolve()) or file.is_symlink():
                raise BundleValidationError('Unsafe payload path')
            if not file.is_file() or file.stat().st_size != expected['bytes'] or sha256(file) != expected['sha256']:
                raise BundleValidationError('Bundle payload integrity hash mismatch')
        for name in POLICIES:
            if sha256(path/'policies'/name) != sha256(ROOT/'config'/name):
                raise BundleValidationError('Frozen policy/schema version mismatch')
        if manifest['policy_versions'] != policy_versions():
            raise BundleValidationError('Manifest policy version mismatch')
        for name, expected in manifest['dependency_versions'].items():
            if importlib.metadata.version(name) != expected:
                raise BundleValidationError('Runtime dependency version mismatch')
        # The release carries the precise source tree even when HEAD has uncommitted work.
        for name, expected in manifest['files'].items():
            if name.startswith('code/'):
                if sha256(ROOT/name[5:]) != expected['sha256']:
                    raise BundleValidationError('Runtime source code mismatch')
        if manifest['max_candidates'] != 200 or manifest['max_k'] != 20:
            raise BundleValidationError('Unsupported serving limits')
        cutoff = pd.Timestamp(manifest['data_as_of'])
        if cutoff.tz is not None or cutoff.day != 1 or cutoff != cutoff.normalize():
            raise BundleValidationError('Invalid historical serving cutoff')
        for name in ['model_labels_end_exclusive','validation_labels_end_exclusive']:
            if pd.Timestamp(manifest['provenance'][name]) > cutoff:
                raise BundleValidationError('Model selection or training uses unavailable future labels')
        self.artifact = load_artifact(path/'model.joblib')
        if self.artifact['feature_schema'] != schema or self.artifact['name'] != manifest['model_name']:
            raise BundleValidationError('Model and feature schema mismatch')
        if manifest['model_version'] != self.artifact['name']+'_'+sha256(path/'model.joblib')[:16]:
            raise BundleValidationError('Model version mismatch')
        self.features = pd.read_parquet(path/'candidate_features.parquet')
        expected_columns = ['cutoff','query_id','customer_id','sku','selected_source']+schema['features']
        if list(self.features.columns) != expected_columns or len(self.features) != manifest['candidate_rows']:
            raise BundleValidationError('Feature row schema/count mismatch')
        if not self.features.cutoff.eq(cutoff).all() or self.features[['customer_id','sku','query_id']].isna().any().any():
            raise BundleValidationError('Invalid customer identities or cutoff')
        if self.features.duplicated(['customer_id','sku']).any():
            raise BundleValidationError('Duplicate recommendation candidates')
        self.values = feature_matrix(self.features,schema)
        self.values.flags.writeable = False
        self.skus = self.features.sku.astype(str).to_numpy()
        self.index = json.loads((path/'customer_index.json').read_text())
        verified_index = {}
        for customer, group in self.features.groupby('customer_id',sort=False):
            rows = group.index.to_numpy()
            if len(rows) > 200 or not np.array_equal(rows,np.arange(rows[0],rows[-1]+1)):
                raise BundleValidationError('Invalid or oversized customer candidate slice')
            if len(set(group.query_id)) != 1:
                raise BundleValidationError('Multiple query cutoffs for one customer')
            verified_index[str(customer)] = [int(rows[0]),int(rows[-1]+1)]
        if self.index != verified_index or len(self.index) != manifest['customers']:
            raise BundleValidationError('Customer lookup index mismatch')
        self.items = pd.read_parquet(path/'items.parquet')
        if not self.items.sku.is_unique or self.items.sku.isna().any() or len(self.items) != manifest['catalog_items']:
            raise BundleValidationError('Invalid item catalog')
        if not self.items.last_observed_at.lt(cutoff).all():
            raise BundleValidationError('Future item display metadata')
        self.display = self.items.set_index('sku').description.where(self.items.set_index('sku').description.notna(),None).to_dict()
        self.fallback = pd.read_parquet(path/'fallback.parquet')
        if len(self.fallback) > 20 or not self.fallback.sku.is_unique or not np.isfinite(self.fallback.ranking_score).all():
            raise BundleValidationError('Invalid popularity fallback')
        catalog = set(self.items.sku)
        if not set(self.skus).issubset(catalog) or not set(self.fallback.sku).issubset(catalog):
            raise BundleValidationError('Candidates outside the historical catalog')
        expected_order = rank_scores(self.fallback.sku,self.fallback.ranking_score)
        if list(self.fallback.sku) != expected_order:
            raise BundleValidationError('Nondeterministic popularity fallback order')
        self.original_policy_paths = [ROOT/'config'/name for name in POLICIES]
        # Configure once, rather than racing global native thread settings per HTTP request.
        self.thread_limits = threadpool_limits(limits=manifest['native_threads'])
        if self.index:
            first = next(iter(self.index))
            self.score_customer(first)  # Validate fitted pipeline and warm native dependencies.

    def score_customer(self,customer_id):
        start,stop = self.index[customer_id]
        return score_values(self.artifact,self.values[start:stop])

    def recommend(self,customer_id,k=10):
        if not isinstance(k,int) or not 1 <= k <= 20:
            raise ValueError('k must be an integer between 1 and 20')
        if customer_id in self.index:
            start,stop = self.index[customer_id]
            skus = self.skus[start:stop]
            scores = self.score_customer(customer_id)
            lookup = dict(zip(skus,scores))
            order = rank_scores(skus,scores,k=k)
            mode,score_kind = 'personalized','raw_model_margin'
        else:
            order = list(self.fallback.sku.iloc[:k])
            lookup = dict(zip(self.fallback.sku,self.fallback.ranking_score))
            mode,score_kind = 'popularity_fallback','distinct_identified_purchasers_30d'
        return {'items':[{'item_id':str(sku),'ranking_score':float(lookup[sku]),'description':self.display[sku]} for sku in order],
                'model_version':self.manifest['model_version'],'release_version':self.manifest['release_id'],
                'data_as_of':self.manifest['data_as_of'],'recommendation_mode':mode,
                'score_kind':score_kind,'scores_are_calibrated_probabilities':False}

    def metadata(self):
        return {key:value for key,value in self.manifest.items() if key != 'files'}
