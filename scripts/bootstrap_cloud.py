"""Prepare local definitions; --apply performs one-time project-admin setup."""
import argparse
import json
from pathlib import Path
import subprocess

from .audit_raw import ROOT
from .cloud_deploy import config,gcloud,request_log_filter,PRIVACY_EXCLUSION


def definitions():
    c=config();service=c['service']
    prefix=f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" '
    labels=[{'key':name,'valueType':'STRING'} for name in ['mode','release','model']]
    extractors={'mode':'EXTRACT(jsonPayload.mode)','release':'EXTRACT(jsonPayload.version)','model':'EXTRACT(jsonPayload.model_version)'}
    metrics=[]
    for name,condition in [
        ('repeat_demo_recommendations','jsonPayload.event="request" AND jsonPayload.route="/recommendations/{customer_id}" AND jsonPayload.status=200'),
        ('repeat_demo_http_errors','jsonPayload.event="request" AND jsonPayload.status>=400'),
        ('repeat_demo_startup_failures','jsonPayload.event="bundle_load_failed"')]:
        metrics.append({'name':name,'description':'Historical demo operational count; no behavioral/customer interpretation.',
            'filter':prefix+'AND '+condition,'metricDescriptor':{'metricKind':'DELTA','valueType':'INT64','unit':'1','labels':labels},
            'labelExtractors':extractors})
    metrics.append({'name':'repeat_demo_app_latency','description':'Application handler latency; excludes client networking and frontend queue time.',
        'filter':prefix+'AND jsonPayload.event="request"','valueExtractor':'EXTRACT(jsonPayload.latency_ms)',
        'metricDescriptor':{'metricKind':'DELTA','valueType':'DISTRIBUTION','unit':'ms','labels':labels},
        'labelExtractors':extractors,'bucketOptions':{'exponentialBuckets':{'numFiniteBuckets':20,'growthFactor':2,'scale':1}}})
    widgets=[]
    common=f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}"'
    for title,metric,aligner in [
        ('Native request latency p95','run.googleapis.com/request_latencies','ALIGN_PERCENTILE_95'),
        ('Native HTTP error request rate','run.googleapis.com/request_count','ALIGN_RATE'),
        ('Container memory utilization p99','run.googleapis.com/container/memory/utilizations','ALIGN_PERCENTILE_99'),
        ('Native container startup p95','run.googleapis.com/container/startup_latencies','ALIGN_PERCENTILE_95'),
        ('Application bundle-load failure count','logging.googleapis.com/user/repeat_demo_startup_failures','ALIGN_SUM')]:
        filter_=common+f' AND metric.type="{metric}"'
        if 'HTTP error' in title:
            filter_+=' AND metric.labels.response_code_class="5xx"'
        widgets.append({'title':title,'xyChart':{'dataSets':[{'plotType':'LINE','timeSeriesQuery':{
            'timeSeriesFilter':{'filter':filter_,'aggregation':{'alignmentPeriod':'60s','perSeriesAligner':aligner}}}}]}})
    success=common+' AND metric.type="logging.googleapis.com/user/repeat_demo_recommendations"'
    aggregation={'alignmentPeriod':'60s','perSeriesAligner':'ALIGN_RATE','crossSeriesReducer':'REDUCE_SUM'}
    widgets.append({'title':'Fallback rate among successful recommendation requests',
        'xyChart':{'dataSets':[{'plotType':'LINE','timeSeriesQuery':{'timeSeriesFilterRatio':{
            'numerator':{'filter':success+' AND metric.labels.mode="popularity_fallback"','aggregation':aggregation},
            'denominator':{'filter':success,'aggregation':aggregation}}}}]}})
    return metrics,{'displayName':'Historical recommendation demo — operational metrics',
                    'gridLayout':{'columns':'2','widgets':widgets}}


def write_definitions(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    metrics,dashboard=definitions()
    for metric in metrics:
        (output/(metric['name']+'.json')).write_text(json.dumps(metric,indent=2)+'\n')
    (output/'dashboard.json').write_text(json.dumps(dashboard,indent=2)+'\n')
    return metrics,dashboard


def ensure(describe,create):
    try:
        return gcloud(*describe,json_output=True)
    except subprocess.CalledProcessError as error:
        if not any(code in error.stderr for code in ['NOT_FOUND','not found','does not exist']):
            raise
        gcloud(*create)


def exclusion_argument(existing):
    # gcloud's dictionary parser uses bool(value): literal "false" is True!
    # Omit for creation; empty value explicitly enables on update.
    return f'name={PRIVACY_EXCLUSION},filter={request_log_filter()}'+(',disabled=' if existing else '')


def apply(project,region,output):
    c=config();metrics,_=write_definitions(output)
    gcloud('services','enable','run.googleapis.com','artifactregistry.googleapis.com','iam.googleapis.com',
        'iamcredentials.googleapis.com','sts.googleapis.com','logging.googleapis.com','monitoring.googleapis.com',
        '--project',project)
    number=gcloud('projects','describe',project,json_output=True)['projectNumber']
    ensure(['artifacts','repositories','describe',c['repository'],'--location',region,'--project',project],
        ['artifacts','repositories','create',c['repository'],'--location',region,'--repository-format=docker',
         '--description=Immutable historical recommendation demo images','--project',project])
    for account in [c['runtime_account'],c['deploy_account']]:
        ensure(['iam','service-accounts','describe',account+'@'+project+'.iam.gserviceaccount.com','--project',project],
            ['iam','service-accounts','create',account,'--display-name=Historical demo '+account,'--project',project])
    deployer=c['deploy_account']+'@'+project+'.iam.gserviceaccount.com'
    runtime=c['runtime_account']+'@'+project+'.iam.gserviceaccount.com'
    member='serviceAccount:'+deployer
    gcloud('artifacts','repositories','add-iam-policy-binding',c['repository'],'--location',region,
        '--project',project,'--member',member,'--role=roles/artifactregistry.writer')
    for role in ['roles/run.developer','roles/run.invoker','roles/logging.viewer','roles/monitoring.viewer']:
        gcloud('projects','add-iam-policy-binding',project,'--member',member,'--role',role,'--condition=None')
    role='repeatDemoSinkViewer'
    ensure(['iam','roles','describe',role,'--project',project],
        ['iam','roles','create',role,'--project',project,'--title=Demo log-routing preflight viewer',
         '--permissions=logging.sinks.list,logging.sinks.get','--stage=GA'])
    gcloud('projects','add-iam-policy-binding',project,'--member',member,
           '--role=projects/'+project+'/roles/'+role,'--condition=None')
    gcloud('iam','service-accounts','add-iam-policy-binding',runtime,'--project',project,
           '--member',member,'--role=roles/iam.serviceAccountUser')
    ensure(['iam','workload-identity-pools','describe',c['wif_pool'],'--location=global','--project',project],
        ['iam','workload-identity-pools','create',c['wif_pool'],'--location=global','--project',project,
         '--display-name=GitHub historical recommendation demo'])
    condition=("assertion.repository_id == '1406464839' && assertion.ref == 'refs/heads/main' && "
               "assertion.sub == 'repo:"+c['github_repository']+":environment:"+c['github_environment']+"'")
    provider=ensure(['iam','workload-identity-pools','providers','describe',c['wif_provider'],
        '--workload-identity-pool',c['wif_pool'],'--location=global','--project',project],
        ['iam','workload-identity-pools','providers','create-oidc',c['wif_provider'],
         '--workload-identity-pool',c['wif_pool'],'--location=global','--project',project,
         '--issuer-uri=https://token.actions.githubusercontent.com',
         '--attribute-mapping=google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref',
         '--attribute-condition',condition])
    if provider is None:
        provider=gcloud('iam','workload-identity-pools','providers','describe',c['wif_provider'],
            '--workload-identity-pool',c['wif_pool'],'--location=global','--project',project,json_output=True)
    if (provider.get('attributeCondition')!=condition or provider.get('disabled') or
        provider.get('oidc',{}).get('issuerUri')!='https://token.actions.githubusercontent.com' or
        provider.get('attributeMapping',{}).get('attribute.repository_id')!='assertion.repository_id'):
        raise ValueError('Existing WIF provider differs from the restricted GitHub trust policy')
    principal=f'principalSet://iam.googleapis.com/projects/{number}/locations/global/workloadIdentityPools/{c["wif_pool"]}/attribute.repository_id/1406464839'
    gcloud('iam','service-accounts','add-iam-policy-binding',deployer,'--project',project,
        '--member',principal,'--role=roles/iam.workloadIdentityUser')
    # Exclude raw URLs in every exporter before any Cloud Run service is created.
    for sink in gcloud('logging','sinks','list','--project',project,json_output=True):
        if sink['name']=='_Required' or sink.get('disabled'):continue
        existing=any(e['name']==PRIVACY_EXCLUSION for e in sink.get('exclusions',[]))
        flag='--update-exclusion' if existing else '--add-exclusion'
        gcloud('logging','sinks','update',sink['name'],'--project',project,flag,exclusion_argument(existing))
    for metric in metrics:
        name=metric['name'];path=Path(output)/(name+'.json')
        ensure(['logging','metrics','describe',name,'--project',project],
            ['logging','metrics','create',name,'--project',project,'--config-from-file',path])
        gcloud('logging','metrics','update',name,'--project',project,'--config-from-file',path)
    dashboards=gcloud('monitoring','dashboards','list','--project',project,json_output=True)
    if not any(d.get('displayName')=='Historical recommendation demo — operational metrics' for d in dashboards):
        gcloud('monitoring','dashboards','create','--project',project,'--config-from-file',Path(output)/'dashboard.json')
    return {'project':project,'region':region,'workload_identity_provider':
        f'projects/{number}/locations/global/workloadIdentityPools/{c["wif_pool"]}/providers/{c["wif_provider"]}',
        'deploy_service_account':deployer,'runtime_service_account':runtime,
        'github_environment':c['github_environment'],'public_access':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default=config()['project']);parser.add_argument('--region',default=config()['region'])
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4/monitoring')
    parser.add_argument('--apply',action='store_true',help='Use an authorized project-admin identity for one-time setup')
    args=parser.parse_args()
    write_definitions(args.output)
    result=apply(args.project,args.region,args.output) if args.apply else {
        'status':'prepared_only','project':args.project,'region':args.region,'monitoring_definitions':str(args.output),
        'configuration':config(),'needs':'Authorized project-admin identity, enabled billing, network access; use --apply after supplying access.'}
    print(json.dumps(result,indent=2))
