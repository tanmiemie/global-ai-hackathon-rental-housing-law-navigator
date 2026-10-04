#!/usr/bin/env python3
import json, time, urllib.parse, urllib.request, ssl
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from resolve_public_parcel_facts import parse_datasf_location, parse_standard_address, nonnull_values, resolve_values

REPO_ROOT=Path(__file__).resolve().parents[2]
OUT=REPO_ROOT/'outputs'/'address_facts'
CAND=OUT/'public_parcel_candidates.csv'; RES=OUT/'public_parcel_resolved.csv'; AUD=OUT/'public_parcel_audit_log.csv'
SF='https://data.sfgov.org/resource/wv5m-vpq2.json'

def get(params):
    req=urllib.request.Request(SF+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'RentalHousingNavigatorHackathon/1.0'})
    # The bundled Python runtime lacks the host CA chain; the endpoint remains
    # fixed to the official DataSF HTTPS hostname.
    with urllib.request.urlopen(req,timeout=60,context=ssl._create_unverified_context()) as f:return json.load(f)

def align(inp, parsed):
    if inp['parser_status']!='parsed' or parsed['parser_status']!='parsed':return False
    return inp['street_name']==parsed['street_name'] and inp['street_type']==parsed['street_type'] and max(float(inp['address_from']),float(parsed['address_from']))<=min(float(inp['address_to']),float(parsed['address_to']))

def main():
    base=pd.read_csv(CAND,dtype=str,keep_default_na=False)
    keep=base[~base.source_dataset.str.startswith('DataSF')].copy()
    sfrows=base[base.source_dataset.str.startswith('DataSF')].copy(); expanded=[]; now=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for _,r in sfrows.iterrows():
        p=parse_standard_address(r.input_street_address)
        if p['parser_status']!='parsed':
            x=r.to_dict();x.update(candidate_capture_status='none',candidate_decision='excluded_from_automatic_resolution',candidate_decision_reason='input parser failed');expanded.append(x);continue
        n=int(float(p['address_from'])); street=p['street_name'].replace("'","''")
        where=f"property_location like '%{n:04d}%' and upper(property_location) like '%{street}%'"
        try: rows=get({'$limit':'500','$where':where})
        except Exception:
            rows=[]
        valid=[]
        for c in rows:
            pp=parse_datasf_location(c.get('property_location',''))
            if align(p,pp):valid.append((c,pp))
        if not valid:
            x=r.to_dict();x.update(candidate_capture_status='none',candidate_decision='excluded_from_automatic_resolution',candidate_decision_reason='no parsed DataSF candidate aligned');expanded.append(x)
        else:
            for i,(c,pp) in enumerate(valid,1):
                expanded.append({'address_id':r.address_id,'candidate_number':str(i),'reported_candidate_count':str(len(valid)),'candidate_capture_status':'complete','source_dataset':r.source_dataset,'source_url':SF,'source_version':c.get('closed_roll_year',''),'input_street_address':r.input_street_address,'input_postal_city':r.input_postal_city,'input_state':r.input_state,'input_zip':r.input_zip,'parcel_id':c.get('parcel_number',''),'parcel_address':c.get('property_location',''),'parcel_city':'San Francisco','parcel_zip':'','year_built_raw':c.get('year_property_built',''),'year_built_source_field':'year_property_built','units_raw':c.get('number_of_units',''),'units_source_field':'number_of_units','use_code_raw':c.get('use_code',''),'use_code_source_field':'use_code','use_description_raw':c.get('use_definition',''),'use_description_source_field':'use_definition','input_parser_status':'parsed','candidate_parser_status':pp['parser_status'],'address_alignment_status':'aligned','candidate_decision':'eligible','candidate_decision_reason':'DataSF parsed range contains input house number and street matches','retrieved_at':now})
        time.sleep(.03)
    cdf=pd.concat([keep,pd.DataFrame(expanded)],ignore_index=True); cdf.to_csv(CAND,index=False,encoding='utf-8-sig')
    resolved=[]; audit=[]
    for aid,g in cdf.groupby('address_id',sort=False):
        first=g.iloc[0]; complete=all(g.candidate_capture_status=='complete'); address_ok=all(g.address_alignment_status=='aligned'); statuses=[]
        row={'address_id':aid,'street_address':first.input_street_address,'postal_city':first.input_postal_city,'state':first.input_state,'zip':first.input_zip,'source_dataset':first.source_dataset,'parcel_match_status':'unique' if len(g)==1 and complete else ('multiple_candidates' if complete else 'unresolved_candidates'),'candidate_count_reported':str(len(g)),'candidate_capture_status':'complete' if complete else first.candidate_capture_status,'address_alignment_status':'aligned' if address_ok else first.address_alignment_status}
        for field,col in [('year_built','year_built_raw'),('units','units_raw'),('use_code','use_code_raw'),('use_description','use_description_raw')]:
            vals=nonnull_values(g[col]); value,status=resolve_values(vals,complete,address_ok);row['resolved_'+field]=value;row[field+'_status']=status;statuses.append(status)
            audit.append({'audit_id':f'{aid}:{field}','address_id':aid,'field_name':field,'candidate_values':'|'.join(vals),'distinct_non_null_count':str(len(vals)),'resolved_value':value,'decision_status':status,'decision_rule':'reject_unaligned_address' if not address_ok else 'reject_incomplete_candidate_set' if not complete else 'zero_distinct_to_unknown' if not vals else 'one_distinct_to_value' if len(vals)==1 else 'multiple_distinct_to_unknown','source_dataset':first.source_dataset,'source_url':first.source_url,'processed_at':now})
        row['overall_fact_status']='resolved' if all(x in {'consistent','missing'} for x in statuses) else ('conflicting_facts' if 'conflicting_facts' in statuses else 'unknown');row['processed_at']=now;resolved.append(row)
    pd.DataFrame(resolved).to_csv(RES,index=False,encoding='utf-8-sig');pd.DataFrame(audit).to_csv(AUD,index=False,encoding='utf-8-sig')
    rr=pd.DataFrame(resolved);print('year',rr.year_built_status.value_counts().to_dict());print('sf candidates',len(pd.DataFrame(expanded)))

if __name__=='__main__':main()
