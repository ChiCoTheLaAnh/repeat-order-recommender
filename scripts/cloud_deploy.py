"""Clean-commit historical releases, immutable image deployment and privacy preflight."""
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

import pandas as pd

from .audit_raw import ROOT, sha256
from .release_bundle import ReleaseBundle

CONFIG_PATH=ROOT/'config/cloud_demo_v1.json'
PRIVACY_EXCLUSION='historical-demo-request-privacy'


def config():
    return json.loads(CONFIG_PATH.read_text())


def clean_commit():
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('Deployable releases require a clean committed worktree')
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()


def validate_deployable_manifest(manifest,expected_commit):
    p=manifest['provenance']
    if p.get('code_worktree_dirty',True):
        raise ValueError('Dirty-source bundles cannot be deployed; rebuild from a clean commit')
    if not re.fullmatch(r'[0-9a-f]{40}',p['code_commit']) or p['code_commit']!=expected_commit:
        raise ValueError('Bundle source commit does not match the release checkout')
    cutoff=pd.Timestamp(manifest['data_as_of'])
    windows=p.get('training_label_windows',[])
    if not windows or any(pd.Timestamp(w['end_exclusive'])>cutoff for w in windows):
        raise ValueError('Missing training-window proof or future training label window')
    expected=list(pd.date_range('2010-03-01','2011-01-01',freq='MS'))
    if [pd.Timestamp(w['cutoff']) for w in windows]!=expected:
        raise ValueError('Expected every original training cutoff, without random row splits')
    if any(pd.Timestamp(w['end_exclusive'])!=pd.Timestamp(w['cutoff'])+pd.offsets.MonthBegin(1) for w in windows):
        raise ValueError('Training label windows changed')
    for name in ['model_labels_end_exclusive','validation_labels_end_exclusive']:
        if pd.Timestamp(p[name])>cutoff:
            raise ValueError('Temporal inconsistency: training/selection labels exceed serving data_as_of')
    if manifest['model_name']!='logistic' or p.get('data_access',{}).get('test_evaluated',False):
        raise ValueError('Release must preserve the selected logistic model and unopened test')
    if p.get('deployable_build') is not True:
        raise ValueError('Rebuild with --deployable to verify committed-source provenance')
    reference=json.loads((ROOT/'config/logistic_artifact_v1.json').read_text())
    if manifest['files']['model.joblib']['sha256']!=reference['joblib_sha256']:
        raise ValueError('Selected logistic model artifact changed')
    if (manifest['files']['policies/ranking_features_v1.json']['sha256']!=reference['feature_schema_sha256']
        or p['model_labels_end_exclusive']!=reference['model_labels_end_exclusive']):
        raise ValueError('Selected logistic feature schema or training-label boundary changed')


def request_log_filter():
    return ('resource.type="cloud_run_revision" AND resource.labels.service_name="'+config()['service']+
            '" AND logName:"run.googleapis.com%2Frequests"')


def verify_log_privacy(sinks):
    missing=[]
    for sink in sinks:
        if sink['name']=='_Required' or sink.get('disabled'):
            continue
        if not any(e.get('name')==PRIVACY_EXCLUSION and e.get('filter')==request_log_filter()
                   and not e.get('disabled') for e in sink.get('exclusions',[])):
            missing.append(sink['name'])
    if missing:
        raise ValueError('Raw URL request-log exclusions missing on sinks: '+', '.join(missing))


def privacy_preflight(project):
    verify_log_privacy(gcloud('logging','sinks','list','--project',project,json_output=True))
    ancestors=gcloud('projects','get-ancestors',project,json_output=True)
    checked=[]
    for ancestor in ancestors:
        kind=ancestor['type'];identifier=ancestor['id']
        if kind not in ['folder','organization']:
            continue
        sinks=gcloud('logging','sinks','list','--'+kind,identifier,json_output=True)
        aggregated=[sink for sink in sinks if sink.get('includeChildren') or sink.get('interceptChildren')]
        verify_log_privacy(aggregated)
        checked.append({'type':kind,'id':identifier,'aggregated_sinks_checked':len(aggregated)})
    return {'project_sinks_checked':True,'ancestor_exporters_checked':checked}


def service_spec(project,region,image,manifest):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',project):
        raise ValueError('Invalid Google project ID')
    if not re.fullmatch(r'[a-z]+-[a-z]+[0-9]',region):
        raise ValueError('Invalid Cloud Run region')
    if not re.fullmatch(re.escape(region+'-docker.pkg.dev/'+project+'/')+r'[a-z0-9_./-]+@sha256:[0-9a-f]{64}',image):
        raise ValueError('Cloud deployment requires an immutable Artifact Registry image digest')
    c=config()
    labels={'purpose':'historical-recommendation-demo','model':'logistic','data-as-of':'2011-05-01',
            'release':manifest['release_id'].removeprefix('release_v1_')[:12],
            'source-commit':manifest['provenance']['code_commit']}
    return {'apiVersion':'serving.knative.dev/v1','kind':'Service',
        'metadata':{'name':c['service'],'labels':labels,
            'annotations':{'run.googleapis.com/ingress':'all','run.googleapis.com/maxScale':'1','run.googleapis.com/minScale':'0','run.googleapis.com/description':
                'Historical recommendation demo; frozen logistic model; serving data_as_of '+manifest['data_as_of']}},
        'spec':{'template':{'metadata':{'labels':labels,'annotations':{
                    'autoscaling.knative.dev/minScale':'0','autoscaling.knative.dev/maxScale':'1',
                    'run.googleapis.com/cpu-throttling':'true','run.googleapis.com/startup-cpu-boost':'false'}},
            'spec':{'serviceAccountName':c['runtime_account']+'@'+project+'.iam.gserviceaccount.com',
                'containerConcurrency':5,'timeoutSeconds':30,'containers':[{'image':image,
                    'ports':[{'containerPort':8080}],
                    'env':[{'name':'BUNDLE_PATH','value':'/bundle'},
                           {'name':'RELEASE_VERSION','value':manifest['release_id']},
                           {'name':'MODEL_VERSION','value':manifest['model_version']}],
                    'resources':{'limits':{'cpu':'1','memory':'1Gi'}},
                    'startupProbe':{'httpGet':{'path':'/ready','port':8080},'initialDelaySeconds':0,
                                    'periodSeconds':2,'timeoutSeconds':1,'failureThreshold':30},
                    'livenessProbe':{'httpGet':{'path':'/health','port':8080},'initialDelaySeconds':10,
                                     'periodSeconds':15,'timeoutSeconds':1,'failureThreshold':3}}]}},
            'traffic':[{'latestRevision':True,'percent':100}]}}


def sdk_env():
    env=os.environ.copy()
    existing=Path(env.get('CLOUDSDK_CONFIG',Path.home()/'.config/gcloud'))
    if not existing.exists() or not os.access(existing,os.W_OK):
        local=ROOT/'.cache/gcloud';local.mkdir(parents=True,exist_ok=True)
        env['CLOUDSDK_CONFIG']=str(local)
    credentials=env.get('GOOGLE_APPLICATION_CREDENTIALS')
    if credentials and Path(credentials).is_file():
        if json.loads(Path(credentials).read_text()).get('type'):
            env['CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE']=credentials
    env['CLOUDSDK_CORE_DISABLE_PROMPTS']='1'
    env['CLOUDSDK_CORE_DISABLE_FILE_LOGGING']='true'
    if env.get('CODEX_PROXY_CERT'):
        env['CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE']=env['CODEX_PROXY_CERT']
    return env


def gcloud(*args,json_output=False):
    executable=shutil.which('gcloud')
    local=ROOT/'.cache/google-cloud-sdk-install/google-cloud-sdk/bin/gcloud'
    if executable is None and local.exists():
        executable=str(local)
    if not executable:
        raise RuntimeError('gcloud is not installed; see docs/cloud-operations.md')
    command=[executable,*map(str,args),'--quiet']
    if json_output:
        command+=['--format=json']
    result=subprocess.run(command,check=True,text=True,capture_output=True,env=sdk_env())
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def package_bundle(bundle_path,output):
    bundle=ReleaseBundle.load(bundle_path)
    validate_deployable_manifest(bundle.manifest,clean_commit())
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    archive=output/(bundle.manifest['release_id']+'.tar.gz')
    with tempfile.NamedTemporaryFile(dir=output,delete=False) as raw:
        temporary=Path(raw.name)
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as tar:
                for file in sorted(Path(bundle_path).rglob('*')):
                    info=tar.gettarinfo(str(file),arcname='bundle/'+str(file.relative_to(bundle_path)))
                    info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                    info.mode=0o555 if file.is_dir() else 0o444
                    with file.open('rb') if file.is_file() else _null_file() as contents:
                        tar.addfile(info,contents)
    if archive.exists():
        if sha256(archive)!=sha256(temporary):
            temporary.unlink();raise ValueError('Immutable archive collision')
        temporary.unlink()
    else:
        temporary.rename(archive);archive.chmod(0o444)
    record={'release_id':bundle.manifest['release_id'],'archive':str(archive),
        'archive_sha256':sha256(archive),'manifest_sha256':sha256(Path(bundle_path)/'manifest.json'),
        'code_commit':bundle.manifest['provenance']['code_commit'],
        'files':bundle.manifest['files']}
    (output/'release_asset.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def _null_file():
    from contextlib import nullcontext
    return nullcontext(None)


def extract_bundle(archive,destination,expected_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}',expected_sha256) or sha256(archive)!=expected_sha256:
        raise ValueError('Release archive SHA256 mismatch')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            p=Path(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='bundle' or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe release archive member')
        tar.extractall(destination,filter='data')
    validate_deployable_manifest(json.loads((destination/'bundle/manifest.json').read_text()),clean_commit())
    bundle=ReleaseBundle.load(destination/'bundle')
    return destination/'bundle'


def prepare_context(bundle_path,destination):
    bundle=ReleaseBundle.load(bundle_path)
    validate_deployable_manifest(bundle.manifest,clean_commit())
    destination=Path(destination)
    if destination.exists():
        raise ValueError('Image context already exists; use a new ignored directory')
    destination.mkdir(parents=True)
    shutil.copytree(bundle_path,destination/'bundle')
    shutil.copyfile(ROOT/'Dockerfile.cloud',destination/'Dockerfile')
    (destination/'build_metadata.json').write_text(json.dumps({
        'release_id':bundle.manifest['release_id'],'code_commit':bundle.manifest['provenance']['code_commit'],
        'bundle_manifest_sha256':sha256(Path(bundle_path)/'manifest.json')},indent=2)+'\n')
    return destination


def validate_image_labels(labels,manifest,manifest_sha):
    expected={'org.opencontainers.image.revision':manifest['provenance']['code_commit'],
              'io.repeat-order.release':manifest['release_id'],
              'io.repeat-order.bundle-manifest-sha256':manifest_sha}
    if any(labels.get(key)!=value for key,value in expected.items()):
        raise ValueError('Image OCI provenance labels do not match the supplied release')


def verify_image_bundle(image,bundle_path):
    """Pull an immutable image and inspect its files without executing it."""
    env=os.environ.copy()
    docker_config=Path(env.get('DOCKER_CONFIG',Path.home()/'.docker'))
    if not docker_config.exists() or not os.access(docker_config,os.W_OK):
        docker_config=ROOT/'.cache/docker';docker_config.mkdir(parents=True,exist_ok=True)
        env['DOCKER_CONFIG']=str(docker_config)
    def docker(*args):
        return subprocess.check_output(['docker',*map(str,args)],env=env,text=True).strip()
    docker('pull',image)
    inspected=json.loads(docker('image','inspect',image))[0]
    manifest=json.loads((Path(bundle_path)/'manifest.json').read_text())
    digest=sha256(Path(bundle_path)/'manifest.json')
    validate_image_labels(inspected['Config'].get('Labels',{}),manifest,digest)
    container=docker('create',image)
    try:
        with tempfile.TemporaryDirectory(prefix='verify-image-') as temporary:
            extracted=Path(temporary)/'bundle'
            docker('cp',container+':/bundle',extracted)
            if sha256(extracted/'manifest.json')!=digest:
                raise ValueError('Image contains a different bundle manifest')
            validate_deployable_manifest(json.loads((extracted/'manifest.json').read_text()),clean_commit())
            ReleaseBundle.load(extracted)
    finally:
        docker('rm',container)
    return {'image_id':inspected['Id'],'bundle_manifest_sha256':digest,'verified_without_execution':True}


def deploy(bundle_path,image,project,region,output):
    bundle=ReleaseBundle.load(bundle_path)
    validate_deployable_manifest(bundle.manifest,clean_commit())
    # This MUST precede service creation and every smoke/load request.
    log_privacy=privacy_preflight(project)
    spec=service_spec(project,region,image,bundle.manifest)
    image_verification=verify_image_bundle(image,bundle_path)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    spec_path=output/'cloud_run_service.json';spec_path.write_text(json.dumps(spec,indent=2)+'\n')
    previous=None
    try:
        previous=gcloud('run','services','describe',config()['service'],'--project',project,'--region',region,json_output=True)
    except subprocess.CalledProcessError as error:
        if 'NOT_FOUND' not in error.stderr and 'Cannot find' not in error.stderr:
            raise
    if previous:
        iam=gcloud('run','services','get-iam-policy',config()['service'],'--project',project,'--region',region,json_output=True)
        if any(binding['role']=='roles/run.invoker' and
               any(member in ['allUsers','allAuthenticatedUsers'] for member in binding.get('members',[]))
               for binding in iam.get('bindings',[])):
            raise ValueError('Existing service has public IAM access; review it before authenticated demo deployment')
    gcloud('run','services','replace',spec_path,'--project',project,'--region',region)
    current=gcloud('run','services','describe',config()['service'],'--project',project,'--region',region,json_output=True)
    record={'project':project,'region':region,'service':config()['service'],'image_digest':image,
        'release_id':bundle.manifest['release_id'],'model_version':bundle.manifest['model_version'],
        'code_commit':bundle.manifest['provenance']['code_commit'],'manifest_sha256':sha256(Path(bundle_path)/'manifest.json'),
        'previous_service':previous,'service_state':current,'url':current['status']['url'],
        'image_verification':image_verification,
        'log_privacy':log_privacy,
        'configuration':spec,'access':'IAM authenticated; not public','test_evaluated':False}
    (output/'deployment.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    pack=sub.add_parser('package');pack.add_argument('--bundle',type=Path,required=True);pack.add_argument('--output',type=Path,default=ROOT/'outputs/release_assets')
    extract=sub.add_parser('extract');extract.add_argument('--archive',type=Path,required=True);extract.add_argument('--sha256',required=True);extract.add_argument('--output',type=Path,required=True)
    context=sub.add_parser('context');context.add_argument('--bundle',type=Path,required=True);context.add_argument('--output',type=Path,required=True)
    render=sub.add_parser('render');render.add_argument('--bundle',type=Path,required=True);render.add_argument('--image',required=True);render.add_argument('--project',default=config()['project']);render.add_argument('--region',default=config()['region']);render.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4/cloud_run_service.json')
    publish=sub.add_parser('deploy');publish.add_argument('--bundle',type=Path,required=True);publish.add_argument('--image',required=True);publish.add_argument('--project',default=config()['project']);publish.add_argument('--region',default=config()['region']);publish.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4')
    args=parser.parse_args()
    if args.command=='package': result=package_bundle(args.bundle,args.output)
    elif args.command=='extract': result={'bundle':str(extract_bundle(args.archive,args.output,args.sha256))}
    elif args.command=='context': result={'context':str(prepare_context(args.bundle,args.output))}
    elif args.command=='render':
        b=ReleaseBundle.load(args.bundle);validate_deployable_manifest(b.manifest,clean_commit())
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(service_spec(args.project,args.region,args.image,b.manifest),indent=2)+'\n');result={'spec':str(args.output)}
    else: result=deploy(args.bundle,args.image,args.project,args.region,args.output)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
