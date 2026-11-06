"""Public demo tests use only synthetic history, never target labels."""
import json
import unittest
from types import SimpleNamespace
import pandas as pd

from scripts import demo_export as demo


def rows():
    result=[]
    for n,customer in enumerate(['PRIVATE_A','PRIVATE_B','PRIVATE_C','PRIVATE_D','PRIVATE_E']):
        for i in range(n+1):
            result.append({'record_id':len(result)+1,'invoice_id':f'PRIVATE_INV_{customer}_{i}',
                'sku':'SKU-A','customer_id':customer,'invoice_date':pd.Timestamp('2011-04-01')+pd.Timedelta(days=i),
                'is_paid_purchase':True,'flag_unresolved_stock_code':False,'stock_code_status':'merchandise',
                'eligible_personalized_history':True,'eligible_customer_labels':True})
    return pd.DataFrame(result)


class DemoExportTests(unittest.TestCase):
    def test_committed_public_export_has_only_allowlisted_fields(self):
        from pathlib import Path
        payload=json.loads((Path(__file__).parents[1]/'web/public/data/demo.json').read_text())
        demo.validate_public(payload)
        self.assertTrue(all(len(p['recommendations'])==20 for p in payload['profiles']))
        for target in [payload['metadata'],payload['profiles'][0],payload['profiles'][0]['recommendations'][0]]:
            target['customer_id']='PRIVATE_LEAK'
            with self.assertRaises(ValueError):demo.validate_public(payload)
            del target['customer_id']

    def test_selection_is_deterministic_past_only_and_unique(self):
        features=pd.DataFrame({'customer_id':['PRIVATE_A','PRIVATE_B','PRIVATE_C','PRIVATE_D','PRIVATE_E'],
            'customer_invoice_count_180':[1,2,3,4,5],
            'customer_days_since_last_purchase_180':[5,50,10,25,1]})
        chosen=demo.select_profiles(features)
        self.assertEqual([x['customer'] for x in chosen],['PRIVATE_A','PRIVATE_C','PRIVATE_E','PRIVATE_B'])
        self.assertEqual(demo.select_profiles(features.sample(frac=1,random_state=4)),chosen)

    def test_history_boundaries_invoice_counting_and_anonymous_exclusion(self):
        paid=rows();first=paid.iloc[[0]].copy()
        first['invoice_date']=pd.Timestamp('2010-11-02');first['invoice_id']='PRIVATE_LOWER'
        duplicate=paid.iloc[[0]].copy();duplicate['sku']='SKU-B'
        boundary=paid.iloc[[0]].copy();boundary['invoice_date']=pd.Timestamp('2011-05-01')
        boundary['invoice_id']='PRIVATE_FUTURE'
        anonymous=paid.iloc[[0]].copy();anonymous['customer_id']=None
        history=demo.history_summary(pd.concat([paid,first,duplicate,boundary,anonymous]),'PRIVATE_A',pd.Timestamp('2011-05-01'))
        self.assertEqual(history['summary']['invoiceCount'],2)
        self.assertEqual(history['summary']['skuCount'],2)
        self.assertEqual(sum(x['invoiceCount'] for x in history['monthly']),2)
        self.assertEqual(history,demo.history_summary(pd.concat([paid,first,duplicate,boundary,anonymous,
            boundary.assign(invoice_date=pd.Timestamp('2020-01-01'),sku='FUTURE-SKU')]),'PRIVATE_A',pd.Timestamp('2011-05-01')))

    def test_public_payload_keeps_model_order_and_has_no_identity_fields(self):
        paid=rows()
        features=[]
        for n,customer in enumerate(['PRIVATE_A','PRIVATE_B','PRIVATE_C','PRIVATE_D','PRIVATE_E']):
            for i,sku in enumerate(['SKU-A','SKU-B']):
                features.append({'customer_id':customer,'sku':sku,'customer_invoice_count_180':n+1,
                    'customer_sku_count_180':1,'customer_days_since_last_purchase_180':30-n,
                    'ci_previously_purchased':int(sku=='SKU-A'),'ci_invoice_count':n+1 if sku=='SKU-A' else 0,
                    'ci_days_since_last_purchase':30-n if sku=='SKU-A' else float('nan'),
                    'source_history':int(i==0),'source_neighbors':int(i==1),'source_recent_popularity':0,'source_long_popularity':0})
        def recommend(customer,k):
            return {'recommendation_mode':'personalized' if customer.startswith('PRIVATE_') else 'popularity_fallback',
                    'score_kind':'raw_model_margin' if customer.startswith('PRIVATE_') else 'distinct_identified_purchasers_30d',
                    'items':[{'item_id':'SKU-B','description':'Product B','ranking_score':.3},
                             {'item_id':'SKU-A','description':'Product A','ranking_score':.2}][:k]}
        bundle=SimpleNamespace(features=pd.DataFrame(features),recommend=recommend,
            manifest={'data_as_of':'2011-05-01T00:00:00','release_id':'release-test','model_version':'logistic-test',
                'provenance':{'code_commit':'a'*40}})
        payload=demo.build_demo(bundle,paid)
        self.assertEqual(payload['defaultProfile'],'demo-typical')
        self.assertEqual([x['sku'] for x in payload['profiles'][0]['recommendations']],['SKU-B','SKU-A'])
        self.assertFalse(payload['profiles'][0]['recommendations'][0]['previouslyPurchased'])
        self.assertIsNone(payload['profiles'][-1]['summary'])
        self.assertIsNone(payload['profiles'][-1]['recommendations'][0]['previouslyPurchased'])
        serialized=json.dumps(payload,allow_nan=False)
        self.assertNotIn('PRIVATE_',serialized)
        self.assertNotIn('customer_id',serialized)
        self.assertNotIn('invoice_id',serialized)
        self.assertNotIn('query_id',serialized)
        future=paid.iloc[[0]].assign(invoice_date=pd.Timestamp('2012-01-01'),sku='FUTURE-SKU')
        self.assertEqual(payload,demo.build_demo(bundle,pd.concat([paid,future])))
