"""One bounded cloud diagnostic; receipts survive setup/warmup failure, no retries."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess

import httpx

from .audit_raw import ROOT, sha256
from .cloud_deploy import APPROVED_RELEASE_PATH, approved_release, config, gcloud, privacy_preflight, load_approved_bundle
from .cloud_verify import failure_category, serving_revision, tls_context, warm_level


def diagnose(bundle_path,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    report={'status':'started','phase':'approval','start_utc':datetime.now(timezone.utc).isoformat(),
        'original_five_client_failure_preserved':True,'cold_start':'not measured',
        'test_evaluated':False,'raw_customer_ids_reported':False}
    # Exclusive creation happens before any network/credential operation.
    with output.open('x') as stream:json.dump(report,stream,indent=2)
    try:
        record=json.loads(APPROVED_RELEASE_PATH.read_text())
        approval=approved_release(bundle_path,record['image_digest'])
        report['approval']=approval
        report['phase']='identity'
        if not gcloud('auth','list','--filter=status:ACTIVE','--format=value(account)'):
            raise ValueError('No active identity')
        c=config();project,region=c['project'],c['region']
        report.update({'project':project,'region':region,'service':c['service']})
        report['phase']='privacy'
        report['log_privacy']=privacy_preflight(project)
        report['phase']='service'
        state=gcloud('run','services','describe',c['service'],'--project',project,
                     '--region',region,json_output=True)
        revision=serving_revision(state);url=state['status']['url']
        if state['spec']['template']['spec']['containers'][0]['image']!=record['image_digest']:
            raise ValueError('Service image differs from approved digest')
        report['revision']=revision
        report['phase']='bundle'
        bundle=load_approved_bundle(bundle_path,record['image_digest'])
        report['phase']='invoker_token'
        account=c['deploy_account']+'@'+project+'.iam.gserviceaccount.com'
        token=gcloud('auth','print-identity-token','--impersonate-service-account',account,
                     '--audiences',url,'--include-email')
        headers={'Authorization':'Bearer '+token}
        report['phase']='metadata'
        with httpx.Client(base_url=url,headers=headers,verify=tls_context(),timeout=30) as client:
            response=client.get('/model');response.raise_for_status();metadata=response.json()
            for key in ['release_id','model_version','data_as_of']:
                if metadata[key]!=bundle.manifest[key]:raise ValueError('Release metadata mismatch')
            if metadata['provenance']['code_commit']!=record['artifact_source_commit']:
                raise ValueError('Artifact source mismatch')
        report['conditions']={'client_os':platform.system(),'client_machine':platform.machine(),
            'visible_cpu_count':os.cpu_count(),'hardware_quota':'unknown',
            'collector_sha256':sha256(Path(__file__)),
            'warm_collector_sha256':sha256(ROOT/'scripts/cloud_verify.py'),
            'dependency_lock_sha256':sha256(ROOT/'uv.lock'),
            'scope':'Synthetic remote HTTPS, includes client networking/TLS; no capacity guarantee',
            'load':{'warmup_requests':20,'requests_per_level':250,'seconds_per_level':20,'k':10},
            'concurrency':5,'request_timeout_seconds':30}
        report['phase']='warm_load'
        report['warm_start_utc']=datetime.now(timezone.utc).isoformat()
        level=asyncio.run(warm_level(url,headers,sorted(bundle.index),5,report['conditions']['load']))
        report['warm_level']=level
        report['status']=level['status']
        report['phase']='warmup' if level['status']=='aborted' else 'finished'
    except Exception as error:
        report['status']='failed'
        report['failure_category']=('setup:GoogleCommandFailed'
            if isinstance(error,subprocess.CalledProcessError) else failure_category(error))
    finally:
        report['end_utc']=datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4/cloudshell-diagnostic-once.json')
    args=parser.parse_args()
    try:report=diagnose(args.bundle,args.output)
    except FileExistsError:
        print(json.dumps({'status':'refused','reason':'Receipt exists; no request sent, no retry'}))
        return 2
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='completed' and report['warm_level']['warm_p95_target_met'] else 1


if __name__=='__main__':raise SystemExit(main())
