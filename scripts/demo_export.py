"""Export a small pseudonymized static demo from the approved historical release.

No labels, test snapshots, future purchase rows or models are published.
"""
import argparse
import json
import math
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .audit_raw import ROOT, sha256
from .cloud_deploy import APPROVED_RELEASE_PATH, load_approved_bundle
from .temporal_snapshots import REQUIRED_COLUMNS, recommendation_eligibility

PROFILE_META=[('demo-light','Lighter history','Fewer purchase invoices'),
    ('demo-typical','Typical history','Middle of the historical range'),
    ('demo-frequent','Frequent purchases','More purchase invoices'),
    ('demo-less-recent','Longer since purchase','A less recent returning customer')]
SOURCE_COLUMNS={'history':'source_history','neighbors':'source_neighbors',
    'recent_popularity':'source_recent_popularity','long_popularity':'source_long_popularity'}


def select_profiles(features):
    keys=['customer_invoice_count_180','customer_days_since_last_purchase_180']
    grouped=features.groupby('customer_id',sort=True)
    if (grouped[keys].nunique(dropna=False)>1).any().any():
        raise ValueError('Customer aggregates vary across candidate rows')
    customers=grouped[keys].first().reset_index().sort_values(keys+['customer_id'],kind='stable')
    if len(customers)<4:raise ValueError('At least four identified profiles required')
    chosen=[str(customers.iloc[i].customer_id) for i in [0,(len(customers)-1)//2,len(customers)-1]]
    remaining=customers.loc[~customers.customer_id.isin(chosen)].sort_values(
        ['customer_days_since_last_purchase_180','customer_id'],ascending=[False,True],kind='stable')
    chosen.append(str(remaining.iloc[0].customer_id))
    return [{'id':alias,'label':label,'subtitle':subtitle,'customer':customer}
        for (alias,label,subtitle),customer in zip(PROFILE_META,chosen)]


def visible_purchases(paid,cutoff):
    # Filter time BEFORE eligibility/aggregation, even when the reader already pushed it down.
    past=paid.loc[paid.invoice_date.notna() & (paid.invoice_date<cutoff)]
    flagged=recommendation_eligibility(past)
    return flagged.loc[flagged.recommendation_eligible_v1 &
        flagged.eligible_personalized_history.fillna(False) & flagged.customer_id.notna()]


def history_summary(paid,customer,cutoff):
    past=visible_purchases(paid,cutoff)
    window=past.loc[past.customer_id.eq(customer) & (past.invoice_date>=cutoff-pd.Timedelta(days=180))]
    if window.empty:raise ValueError('Selected customer has no eligible history in window')
    first=(cutoff-pd.Timedelta(days=180)).to_period('M').to_timestamp()
    months=pd.date_range(first,cutoff-pd.offsets.MonthBegin(1),freq='MS')
    monthly=[]
    for month in months:
        subset=window.loc[(window.invoice_date>=month) & (window.invoice_date<month+pd.offsets.MonthBegin(1))]
        monthly.append({'month':month.strftime('%Y-%m'),'label':month.strftime('%b %Y'),
            'invoiceCount':int(subset.invoice_id.nunique())})
    return {'summary':{'invoiceCount':int(window.invoice_id.nunique()),'skuCount':int(window.sku.nunique()),
        'daysSinceLastPurchase':float((cutoff-window.invoice_date.max()).total_seconds()/86400)},'monthly':monthly}


def build_demo(bundle,paid):
    cutoff=pd.Timestamp(bundle.manifest['data_as_of'])
    if cutoff!=pd.Timestamp('2011-05-01'):raise ValueError('Only the frozen serving cutoff is supported')
    # No target labels or future catalog metadata are accepted by this interface.
    past=visible_purchases(paid,cutoff)
    profiles=[]
    for selected in select_profiles(bundle.features):
        customer=selected['customer']
        histories=history_summary(past,customer,cutoff)
        f=bundle.features.loc[bundle.features.customer_id.eq(customer)].set_index('sku')
        first=f.iloc[0]
        if (histories['summary']['invoiceCount']!=int(first.customer_invoice_count_180)
            or histories['summary']['skuCount']!=int(first.customer_sku_count_180)
            or not math.isclose(histories['summary']['daysSinceLastPurchase'],
                                float(first.customer_days_since_last_purchase_180),abs_tol=.001)):
            raise ValueError('History does not agree with approved serving features')
        response=bundle.recommend(customer,20)
        recommendations=[]
        for rank,item in enumerate(response['items'],1):
            row=f.loc[item['item_id']]
            age=row.ci_days_since_last_purchase
            recommendations.append({'rank':rank,'sku':item['item_id'],
                'name':item['description'] or 'Product '+item['item_id'],
                'score':float(item['ranking_score']),'scoreKind':response['score_kind'],
                'previouslyPurchased':bool(row.ci_previously_purchased),
                'purchaseInvoiceCount':int(row.ci_invoice_count),
                'daysSinceLastPurchase':float(age) if pd.notna(age) else None,
                'sources':[name for name,col in SOURCE_COLUMNS.items() if bool(row[col])]})
        profiles.append({k:v for k,v in selected.items() if k!='customer'} |
            {'mode':'personalized',**histories,'recommendations':recommendations})
    # This sentinel is only used locally, never serialized as a customer identity.
    response=bundle.recommend('public-demo-unknown-customer',20)
    profiles.append({'id':'demo-guest','label':'Unknown customer','subtitle':'No personal history available',
        'mode':'popularity_fallback','summary':None,'monthly':[],
        'recommendations':[{'rank':rank,'sku':item['item_id'],'name':item['description'] or 'Product '+item['item_id'],
            'score':float(item['ranking_score']),'scoreKind':response['score_kind'],'previouslyPurchased':None,
            'purchaseInvoiceCount':None,'daysSinceLastPurchase':None,'sources':['recent_popularity']}
            for rank,item in enumerate(response['items'],1)]})
    result={'schemaVersion':'public_demo_v1','defaultProfile':'demo-typical',
        'metadata':{'dataAsOf':cutoff.isoformat(),'windowStart':(cutoff-pd.Timedelta(days=180)).isoformat(),
            'windowDays':180,'releaseId':bundle.manifest['release_id'],'modelVersion':bundle.manifest['model_version'],
            'artifactSourceCommit':bundle.manifest['provenance']['code_commit'],
            'inferenceMode':'precomputed','repository':'https://github.com/ChiCoTheLaAnh/repeat-order-recommender'},
        'quality':{'heuristicNdcg':.3351,'logisticNdcg':.378038,'xgbNdcg':.379499,
            'primaryQueries':1817,'primaryQueryFraction':.2359,
            'description':'Validation queries with at least one known-catalog target; no measured business lift.'},
        'profiles':profiles}
    validate_public(result)
    return result


def validate_public(result):
    # Exact shape allowlists prevent accidentally dumping full frames or model manifests.
    allowed={
        'root':{'schemaVersion','defaultProfile','metadata','quality','profiles'},
        'metadata':{'dataAsOf','windowStart','windowDays','releaseId','modelVersion','artifactSourceCommit','inferenceMode','repository'},
        'quality':{'heuristicNdcg','logisticNdcg','xgbNdcg','primaryQueries','primaryQueryFraction','description'},
        'profile':{'id','label','subtitle','mode','summary','monthly','recommendations'},
        'summary':{'invoiceCount','skuCount','daysSinceLastPurchase'},
        'month':{'month','label','invoiceCount'},
        'item':{'rank','sku','name','score','scoreKind','previouslyPurchased','purchaseInvoiceCount','daysSinceLastPurchase','sources'}}
    def shape(obj,key):
        if set(obj)!=allowed[key]:raise ValueError('Unexpected public export fields: '+key)
    shape(result,'root');shape(result['metadata'],'metadata');shape(result['quality'],'quality')
    expected={p[0] for p in PROFILE_META}|{'demo-guest'}
    if {p['id'] for p in result['profiles']}!=expected or len(result['profiles'])!=5:
        raise ValueError('Unexpected public profiles')
    for p in result['profiles']:
        shape(p,'profile')
        if p['summary'] is not None:shape(p['summary'],'summary')
        for month in p['monthly']:shape(month,'month')
        skus=[]
        for rank,item in enumerate(p['recommendations'],1):
            shape(item,'item');skus.append(item['sku'])
            if item['rank']!=rank or not math.isfinite(item['score']):raise ValueError('Invalid ranking')
        if len(skus)!=len(set(skus)):raise ValueError('Duplicate recommendation SKU')
    json.dumps(result,allow_nan=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--paid',type=Path,default=ROOT/'outputs/cleaning/paid_purchases.parquet')
    parser.add_argument('--output',type=Path,default=ROOT/'web/public/data/demo.json')
    args=parser.parse_args()
    approval=json.loads(APPROVED_RELEASE_PATH.read_text())
    bundle=load_approved_bundle(args.bundle,approval['image_digest'])
    if sha256(args.paid)!=bundle.manifest['provenance']['input_sha256']['paid_purchases']:
        raise ValueError('Cleaning input differs from approved release; do not retrain or substitute')
    cutoff=pd.Timestamp(bundle.manifest['data_as_of'])
    paid=pq.read_table(args.paid,columns=REQUIRED_COLUMNS,filters=[('invoice_date','<',cutoff)]).to_pandas()
    result=build_demo(bundle,paid)
    if any(len(p['recommendations'])!=20 for p in result['profiles']):
        raise ValueError('Production demo requires twenty stored recommendations per profile')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    receipt=ROOT/'outputs/demo-export/export_receipt.json'
    receipt.parent.mkdir(parents=True,exist_ok=True)
    receipt.write_text(json.dumps({'public_json_sha256':sha256(args.output),
        'source_manifest_sha256':approval['manifest_sha256'],'exporter_sha256':sha256(Path(__file__)),
        'release_id':approval['release_id'],'test_labels_read':False},indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'profiles':5,'recommendations':100,
                      'bytes':args.output.stat().st_size,'test_labels_read':False,'live_inference':False}))


if __name__=='__main__':main()
