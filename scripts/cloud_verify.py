"""Bounded remote HTTP verification; cold-start evidence is separate from warm traffic."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime,timedelta,timezone
from itertools import count
from functools import wraps
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import quote

import httpx
import numpy as np

from .audit_raw import ROOT
from .cloud_deploy import config,gcloud,privacy_preflight
from .load_test import request_mode,summarize
from .release_bundle import ReleaseBundle


def tls_context():
    return ssl.create_default_context(cafile=os.environ.get('CODEX_PROXY_CERT'))


def sanitized_http_errors(function):
    @wraps(function)
    def checked(*args,**kwargs):
        try:
            return function(*args,**kwargs)
        except httpx.HTTPStatusError as error:
            raise RuntimeError('Cloud HTTP check failed; status='+str(error.response.status_code)+'; customer URL omitted') from None
        except httpx.HTTPError as error:
            raise RuntimeError('Cloud transport check failed: '+type(error).__name__+'; customer URL omitted') from None
    return checked


@sanitized_http_errors
def smoke(client,bundle):
    for path in ['/health','/ready','/model']:
        response=client.get(path);response.raise_for_status()
    metadata=client.get('/model').json()
    assert metadata['release_id']==bundle.manifest['release_id']
    assert metadata['data_as_of']==bundle.manifest['data_as_of']
    assert metadata['model_version']==bundle.manifest['model_version']
    assert metadata['provenance']['code_commit']==bundle.manifest['provenance']['code_commit']
    customers=sorted(bundle.index)
    tested=[]
    for position in sorted(set([0,len(customers)//2,len(customers)-1])):
        customer=customers[position]
        response=client.get('/recommendations/'+customer,params={'k':10});response.raise_for_status()
        actual=response.json();expected=bundle.recommend(customer,10)
        assert [i['item_id'] for i in actual['items']]==[i['item_id'] for i in expected['items']]
        np.testing.assert_allclose([i['ranking_score'] for i in actual['items']],
            [i['ranking_score'] for i in expected['items']],atol=1e-10,rtol=1e-10)
        for key in ['recommendation_mode','release_version','model_version','data_as_of','scores_are_calibrated_probabilities']:
            assert actual[key]==expected[key]
        tested.append(position)
    fallback=client.get('/recommendations/unknown-cloud-smoke',params={'k':10});fallback.raise_for_status()
    assert fallback.json()==bundle.recommend('unknown-cloud-smoke',10)
    assert client.get('/recommendations/unknown-cloud-smoke?k=21').status_code==422
    return {'passed':True,'known_customer_index_positions_checked':tested,
        'unknown_mode':fallback.json()['recommendation_mode'],'release_id':metadata['release_id'],
        'code_commit':metadata['provenance']['code_commit'],'raw_customer_ids_reported':False}


def serving_revision(state):
    traffic=[entry for entry in state['status'].get('traffic',[]) if entry.get('percent',0)>0]
    if len(traffic)!=1 or traffic[0].get('percent')!=100 or not traffic[0].get('revisionName'):
        raise ValueError('Bounded verification requires one resolved revision receiving 100% traffic')
    return traffic[0]['revisionName']


async def warm_level(url,headers,customers,concurrency,load):
    latencies,statuses,modes=[],[],[]
    ids=set();sequence=count()
    limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=url,headers=headers,verify=tls_context(),timeout=30,limits=limits) as client:
        for number in range(load['warmup_requests']):
            response=await client.get('/recommendations/'+customers[number%len(customers)],params={'k':load['k']})
            response.raise_for_status()
        started=time.perf_counter();deadline=started+load['seconds_per_level']
        async def worker():
            while time.perf_counter()<deadline:
                number=next(sequence)
                if number>=load['requests_per_level']:return
                expected=request_mode(number,.1)
                customer='unknown-cloud-load' if expected=='popularity_fallback' else customers[number%len(customers)]
                before=time.perf_counter()
                try:
                    result=await client.get('/recommendations/'+customer,params={'k':load['k']})
                    status=result.status_code
                    ids.add(result.headers.get('X-Instance-Start-Id','unavailable'))
                    if status==200:
                        body=result.json()
                        if body['recommendation_mode']!=expected or len(body['items'])!=load['k']:status=-1
                except (httpx.HTTPError,ValueError,KeyError):status=0
                latencies.append(1000*(time.perf_counter()-before));statuses.append(status);modes.append(expected)
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        result=summarize(latencies,statuses,time.perf_counter()-started)
        result.update({'concurrency':concurrency,'attempted_mode_counts':dict(Counter(modes)),
            'instance_start_ids_seen':sorted(ids),'scope':'remote Cloud Run HTTP; includes client networking/TLS',
            'requests_cap':load['requests_per_level'],'duration_cap_seconds':load['seconds_per_level']})
        p95=result['latency_all_attempts']['p95_ms'] if result['latency_all_attempts'] else float('inf')
        result['warm_p95_target_ms']=250
        result['warm_p95_target_met']=p95<=250 and result['error_rate']==0
        return result


def native_metrics(project,region,revision,start,end):
    access=gcloud('auth','print-access-token')
    base='https://monitoring.googleapis.com/v3/projects/'+project
    results={}
    with httpx.Client(headers={'Authorization':'Bearer '+access},verify=tls_context(),timeout=20) as client:
        for name in ['run.googleapis.com/container/startup_latencies','run.googleapis.com/container/memory/utilizations',
                     'run.googleapis.com/request_latencies','run.googleapis.com/request_count']:
            descriptor=client.get(base+'/metricDescriptors/'+quote(name,safe=''))
            descriptor.raise_for_status()
            filter_=f'metric.type="{name}" AND resource.labels.service_name="{config()["service"]}" AND resource.labels.location="{region}" AND resource.labels.revision_name="{revision}"'
            query=client.get(base+'/timeSeries',params={'filter':filter_,'interval.startTime':start,
                'interval.endTime':end,'pageSize':1000})
            query.raise_for_status()
            data=query.json()
            results[name]={'unit':descriptor.json().get('unit'),'time_series':data.get('timeSeries',[]),
                          'more_pages':bool(data.get('nextPageToken')),
                          'interpretation':'Native exported samples; startup distributions are container startup, not client-perceived cold HTTP latency. Memory utilization is not process RSS.'}
    return results


@sanitized_http_errors
def verify(url,bundle_path,output,project,region,smoke_only=False,cold_zero_evidence=None):
    if not re.fullmatch(r'https://[a-z0-9.-]+\.run\.app',url):
        raise ValueError('Expected an HTTPS Cloud Run service URL')
    # Privacy is checked before the very first known-customer request.
    privacy=privacy_preflight(project)
    state=gcloud('run','services','describe',config()['service'],'--project',project,'--region',region,json_output=True)
    if state['status']['url']!=url:raise ValueError('URL does not match the configured project service')
    revision=serving_revision(state)
    bundle=ReleaseBundle.load(bundle_path)
    token=os.environ.get('CLOUD_RUN_ID_TOKEN')
    if not token:token=gcloud('auth','print-identity-token')
    headers={'Authorization':'Bearer '+token}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    start=datetime.now(timezone.utc)
    with httpx.Client(base_url=url,headers=headers,verify=tls_context(),timeout=60) as client:
        before=time.perf_counter();first=client.get('/recommendations/'+sorted(bundle.index)[0],params={'k':10})
        first_ms=1000*(time.perf_counter()-before);first.raise_for_status()
        zero=None
        if cold_zero_evidence:
            zero=json.loads(Path(cold_zero_evidence).read_text())
            seen=datetime.fromisoformat(zero['observed_at'].replace('Z','+00:00'))
            if (zero.get('project')!=project or zero.get('service')!=config()['service'] or zero.get('region')!=region
                or zero.get('active_instances')!=0 or not 0<=(start-seen).total_seconds()<=120):
                raise ValueError('Cold experiment requires recent, matching, observed zero-instance evidence')
        uptime=float(first.headers.get('X-Process-Uptime-Seconds','inf'))
        confirmed=bool(zero and uptime<=10 and first.headers.get('X-Instance-Start-Id'))
        cold={'first_request_ms':first_ms,'confirmed_client_cold_start_ms':first_ms if confirmed else None,
            'status':'confirmed_with_zero_instance_evidence' if confirmed else 'unconfirmed_first_request_not_claimed_cold',
            'process_uptime_seconds':uptime if np.isfinite(uptime) else None,
            'instance_start_id':first.headers.get('X-Instance-Start-Id'),
            'zero_instance_evidence':zero,
            'limitation':'A new instance ID or first request alone does not prove cold start. Confirm zero instances immediately before an isolated request; no concurrent probes/clients. Native startup metrics are reported separately.'}
        smoke_result=smoke(client,bundle)
        startup=client.get('/ready').json()
    levels=[] if smoke_only else [asyncio.run(warm_level(url,headers,sorted(bundle.index),n,config()['load'])) for n in [1,5]]
    with httpx.Client(base_url=url,headers=headers,verify=tls_context(),timeout=30) as client:
        memory_after=client.get('/ready');memory_after.raise_for_status();memory_after=memory_after.json()
    end=datetime.now(timezone.utc)
    metrics={};metrics_status='not_requested_for_smoke_only'
    if not smoke_only:
        try:
            metrics=native_metrics(project,region,revision,
                (start-timedelta(minutes=30)).isoformat(),end.isoformat())
            metrics_status='collected; export may lag several minutes; empty samples mean unavailable'
        except Exception as error:
            metrics_status='unavailable: '+type(error).__name__
    report={'status':'completed','url':url,'project':project,'region':region,'service':config()['service'],
        'release_id':bundle.manifest['release_id'],'revision':revision,
        'service_configuration':state['spec'],'start_utc':start.isoformat(),'end_utc':end.isoformat(),
        'log_privacy':privacy,
        'smoke':smoke_result,'cold_start':cold,'warm_levels':levels,'startup_readiness':startup,
        'memory_after_load':memory_after,'native_metrics_status':metrics_status,'native_metrics':metrics,
        'conditions':{'client_location':'This script runs on the invoking runner; remote results include its network path.',
            'workload':'Synthetic 90% stored-customer queries / 10% unknown fallback; no real customer behavior.',
            'limits':config()['load'],'request_billing':True,'min_instances':0,'max_instances':1,
            'cold':'Only a separately isolated, zero-instance-evidenced request is classified cold.'},'test_evaluated':False}
    (output/'cloud_verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['status','url','region','smoke','cold_start','warm_levels','native_metrics_status']},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True);parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--project',default=config()['project']);parser.add_argument('--region',default=config()['region'])
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4');parser.add_argument('--smoke-only',action='store_true')
    parser.add_argument('--cold-zero-evidence',type=Path,help='Saved recent zero-instance observation; otherwise first-request cold status stays unconfirmed')
    args=parser.parse_args();verify(args.url,args.bundle,args.output,args.project,args.region,args.smoke_only,args.cold_zero_evidence)
