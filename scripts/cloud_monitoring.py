"""Prepare scoped infrastructure alerts and inspect configuration without demo traffic."""
import argparse
import json
from pathlib import Path
import re

import httpx

from .audit_raw import ROOT
from .bootstrap_cloud import definitions
from .cloud_deploy import config, gcloud
from .cloud_verify import failure_category, tls_context


def alert_policies(channel):
    c=config()
    if not re.fullmatch(re.escape('projects/'+c['project']+'/notificationChannels/')+r'[0-9]+',channel):
        raise ValueError('Expected a notification channel resource in the configured project')
    common=('resource.type="cloud_run_revision" AND resource.labels.service_name="'+c['service']+
            '" AND resource.labels.location="'+c['region']+'"')
    policies=[]
    for title,metric,extra in [
        ('Historical demo: service 5xx','run.googleapis.com/request_count',' AND metric.labels.response_code_class="5xx"'),
        ('Historical demo: bundle load failed','logging.googleapis.com/user/repeat_demo_startup_failures','')]:
        policies.append({'displayName':title,'enabled':True,'combiner':'OR',
            'notificationChannels':[channel],'userLabels':{'purpose':'historical-demo'},
            'documentation':{'mimeType':'text/markdown','content':
                'Historical demo infrastructure signal. Inspect release/version and sanitized logs. '
                'Synthetic traffic is not customer behavior; missing samples are unknown, not healthy. '
                'See docs/cloud-operations.md; no automatic model or traffic changes.'},
            'conditions':[{'displayName':title,'conditionThreshold':{
                'filter':common+' AND metric.type="'+metric+'"'+extra,
                'comparison':'COMPARISON_GT','thresholdValue':0,'duration':'0s',
                'aggregations':[{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_SUM',
                                 'crossSeriesReducer':'REDUCE_SUM'}],'trigger':{'count':1}}}]})
    return policies


def monitoring_client():
    token=gcloud('auth','print-access-token')
    return httpx.Client(base_url='https://monitoring.googleapis.com/v3/',
        headers={'Authorization':'Bearer '+token},verify=tls_context(),timeout=30)


def get(client,path):
    response=client.get(path);response.raise_for_status();return response.json()


def list_resources(client,path,key):
    results=[];page=None
    while True:
        response=client.get(path,params={'pageToken':page} if page else {})
        response.raise_for_status();body=response.json();results.extend(body.get(key,[]))
        page=body.get('nextPageToken')
        if not page:return results


def verified_channel(client,channel):
    state=get(client,channel)
    if state.get('verificationStatus')!='VERIFIED' or state.get('enabled') is False:
        raise ValueError('Notification channel must be enabled and verified')
    return state


def apply_alerts(channel):
    policies=alert_policies(channel);parent='projects/'+config()['project']
    with monitoring_client() as client:
        verified_channel(client,channel)
        existing=list_resources(client,parent+'/alertPolicies','alertPolicies')
        names=[]
        for policy in policies:
            matches=[p for p in existing if p.get('displayName')==policy['displayName']]
            if len(matches)>1:raise ValueError('Ambiguous existing alert policies')
            if matches:
                path=matches[0]['name'];payload={**policy,'name':path}
                response=client.patch(path,params={'updateMask':
                    'display_name,enabled,combiner,notification_channels,user_labels,documentation,conditions'},json=payload)
            else:response=client.post(parent+'/alertPolicies',json=policy)
            response.raise_for_status();names.append(response.json()['name'])
    return {'status':'configured','policies':names,'notification_channel':channel,
            'notification_delivery_verified':False,'note':'Configuration is not an end-to-end notification test'}


def inspect_monitoring(channel):
    """Inventory only: existence is not sample availability or notification delivery."""
    expected=alert_policies(channel);parent='projects/'+config()['project']
    with monitoring_client() as client:
        channel_state=get(client,channel)
        dashboards=list_resources(client,'https://monitoring.googleapis.com/v1/'+parent+'/dashboards','dashboards')
        policies=list_resources(client,parent+'/alertPolicies','alertPolicies')
    metric_defs,_=definitions();checks=[]
    for definition in metric_defs:
        actual=gcloud('logging','metrics','describe',definition['name'],'--project',config()['project'],json_output=True)
        checks.append({'name':definition['name'],'filter_matches':actual.get('filter')==definition['filter'],
            'label_extractors_match':actual.get('labelExtractors')==definition['labelExtractors']})
    alerts=[]
    for policy in expected:
        matches=[p for p in policies if p.get('displayName')==policy['displayName']]
        actual=matches[0] if len(matches)==1 else {}
        thresholds=[c.get('conditionThreshold') for c in actual.get('conditions',[])]
        alerts.append({'display_name':policy['displayName'],'unique':len(matches)==1,
            'enabled':actual.get('enabled') is True,'channel_matches':actual.get('notificationChannels')==[channel],
            'condition_matches':thresholds==[policy['conditions'][0]['conditionThreshold']]})
    return {'status':'inventory_only','log_metrics':checks,'alerts':alerts,
        'dashboard_present':any(d.get('displayName')=='Historical recommendation demo — operational metrics' for d in dashboards),
        'channel_enabled':channel_state.get('enabled') is not False,
        'channel_verified':channel_state.get('verificationStatus')=='VERIFIED',
        'notification_delivery_verified':False,'sample_availability':'not checked; missing samples remain unknown'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','apply','inspect'])
    parser.add_argument('--channel',required=True,help='Existing verified channel resource; never an email address')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone4/monitoring-followup.json')
    args=parser.parse_args()
    try:
        result=({'status':'prepared_only','policies':alert_policies(args.channel)} if args.command=='prepare'
            else apply_alerts(args.channel) if args.command=='apply' else inspect_monitoring(args.channel))
    except Exception as error:
        result={'status':'failed','failure_category':failure_category(error)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 1 if result['status']=='failed' else 0


if __name__=='__main__':raise SystemExit(main())
