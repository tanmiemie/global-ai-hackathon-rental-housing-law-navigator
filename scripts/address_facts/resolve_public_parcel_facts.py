#!/usr/bin/env python3
"""Deterministic, conservative public-parcel fact resolver.

Never selects among unresolved parcel candidates. Conflicting or incompletely
captured candidate sets resolve to unknown and remain fully visible in audit.
"""
import re
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

REPO_ROOT=Path(__file__).resolve().parents[2]
OUT=REPO_ROOT/'outputs'/'address_facts'
INP=OUT/'address_public_parcel_observations.csv'
CAND=OUT/'public_parcel_candidates.csv'
RES=OUT/'public_parcel_resolved.csv'
AUD=OUT/'public_parcel_audit_log.csv'

STREET_TYPES={'ST':'ST','AVE':'AVE','AV':'AVE','BLVD':'BLVD','BL':'BLVD','DR':'DR','RD':'RD','WAY':'WAY','WY':'WAY','PL':'PL','CT':'CT','TER':'TER','LN':'LN'}

def clean_number(x):
    x=(x or '').lstrip('0')
    return x or '0'

def parse_datasf_location(value):
    """Parse DataSF fixed-width property_location without guessing.

    Examples:
      0000 3515 FILLMORE ST0000 -> 3515,3515,FILLMORE,ST,0000
      2053 2051 DIVISADERO ST0000 -> 2051,2053,DIVISADERO,ST,0000
    """
    raw=(value or '').strip().upper()
    m=re.match(r'^\s*(\d{4})\s+(\d{1,4})\s+(.+?)\s+(ST|AV|AVE|BL|BLVD|DR|RD|WY|WAY|PL|CT|TER|LN)(\d{0,4})\s*$',raw)
    if not m:
        return {'parser_status':'failed','address_from':'','address_to':'','street_name':'','street_type':'','unit_code':''}
    n1,n2,name,stype,unit=m.groups(); a=int(n1); b=int(n2)
    if a==0:a=b
    lo,hi=sorted((a,b))
    return {'parser_status':'parsed','address_from':str(lo),'address_to':str(hi),'street_name':re.sub(r'\s+',' ',name).strip(),'street_type':STREET_TYPES.get(stype,stype),'unit_code':unit or ''}

def parse_standard_address(value):
    raw=(value or '').strip().upper()
    m=re.match(r'^\s*(\d+(?:\.\d+)?)(?:\s*[-/]\s*(\d+(?:\.\d+)?))?\s+(.+?)\s+(ST|AVE|AV|BLVD|BL|DR|RD|WAY|WY|PL|CT|TER|LN)\b',raw)
    if not m:return {'parser_status':'failed','address_from':'','address_to':'','street_name':'','street_type':'','unit_code':''}
    a,b,name,stype=m.groups(); b=b or a
    return {'parser_status':'parsed','address_from':clean_number(a),'address_to':clean_number(b),'street_name':re.sub(r'\s+',' ',name).strip(),'street_type':STREET_TYPES.get(stype,stype),'unit_code':''}

def address_alignment(row):
    left=parse_standard_address(row['street_address'])
    right=parse_datasf_location(row['parcel_address']) if row['source_dataset'].startswith('DataSF') else parse_standard_address(row['parcel_address'])
    if left['parser_status']!='parsed' or right['parser_status']!='parsed':return left,right,'parser_failed'
    street_ok=(left['street_name']==right['street_name'] and left['street_type']==right['street_type'])
    overlap=max(float(left['address_from']),float(right['address_from'])) <= min(float(left['address_to']),float(right['address_to']))
    return left,right,('aligned' if street_ok and overlap else 'not_aligned')

def nonnull_values(series):
    vals=[]
    for x in series:
        s=str(x).strip()
        if s and s.lower() not in {'nan','none','null','unknown','0.0'}: vals.append(s)
    return sorted(set(vals))

def resolve_values(values, candidate_complete, address_ok):
    if not address_ok:return 'unknown','address_not_aligned'
    if not candidate_complete:return 'unknown','candidate_set_incomplete'
    if len(values)==0:return 'unknown','missing'
    if len(values)==1:return values[0],'consistent'
    return 'unknown','conflicting_facts'

def main():
    df=pd.read_csv(INP,dtype=str,keep_default_na=False)
    now=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    candidates=[]
    for _,r in df.iterrows():
        left,right,align=address_alignment(r)
        reported=int(r['parcel_candidate_count'] or 0)
        complete=(r['parcel_match_status']=='unique' and reported<=1)
        candidates.append({
          'address_id':r['address_id'],'candidate_number':'1','reported_candidate_count':str(reported),
          'candidate_capture_status':'complete' if complete else ('none' if reported==0 else 'representative_only'),
          'source_dataset':r['source_dataset'],'source_url':r['source_url'],'source_version':r['source_version'],
          'input_street_address':r['street_address'],'input_postal_city':r['postal_city'],'input_state':r['state'],'input_zip':r['zip'],
          'parcel_id':r['parcel_id'],'parcel_address':r['parcel_address'],'parcel_city':r['parcel_city'],'parcel_zip':r['parcel_zip'],
          'year_built_raw':r['parcel_year_built_raw'],'year_built_source_field':r['parcel_year_built_field'],
          'units_raw':r['parcel_units_raw'],'units_source_field':r['parcel_units_field'],
          'use_code_raw':r['parcel_use_code_raw'],'use_code_source_field':r['parcel_use_code_field'],
          'use_description_raw':r['parcel_use_description_raw'],'use_description_source_field':r['parcel_use_description_field'],
          'input_parser_status':left['parser_status'],'candidate_parser_status':right['parser_status'],'address_alignment_status':align,
          'candidate_decision':'eligible' if complete and align=='aligned' else 'excluded_from_automatic_resolution',
          'candidate_decision_reason':'unique aligned parcel' if complete and align=='aligned' else ('candidate set not fully captured' if not complete else align),
          'retrieved_at':r['retrieved_at'] or now})
    cdf=pd.DataFrame(candidates)
    cdf.to_csv(CAND,index=False,encoding='utf-8-sig')

    resolved=[]; audit=[]
    for aid,g in cdf.groupby('address_id',sort=False):
        first=g.iloc[0]; complete=all(g['candidate_capture_status']=='complete'); address_ok=all(g['address_alignment_status']=='aligned')
        fields=[('year_built','year_built_raw'),('units','units_raw'),('use_code','use_code_raw'),('use_description','use_description_raw')]
        row={'address_id':aid,'street_address':first['input_street_address'],'postal_city':first['input_postal_city'],'state':first['input_state'],'zip':first['input_zip'],'source_dataset':first['source_dataset'],'parcel_match_status':df.loc[df.address_id==aid,'parcel_match_status'].iloc[0],'candidate_count_reported':first['reported_candidate_count'],'candidate_capture_status':first['candidate_capture_status'],'address_alignment_status':first['address_alignment_status']}
        statuses=[]
        for field,col in fields:
            vals=nonnull_values(g[col]); value,status=resolve_values(vals,complete,address_ok)
            row['resolved_'+field]=value; row[field+'_status']=status; statuses.append(status)
            audit.append({'audit_id':f'{aid}:{field}','address_id':aid,'field_name':field,'candidate_values':'|'.join(vals),'distinct_non_null_count':str(len(vals)),'resolved_value':value,'decision_status':status,'decision_rule':('reject_unaligned_address' if not address_ok else 'reject_incomplete_candidate_set' if not complete else 'zero_distinct_to_unknown' if not vals else 'one_distinct_to_value' if len(vals)==1 else 'multiple_distinct_to_unknown'),'source_dataset':first['source_dataset'],'source_url':first['source_url'],'processed_at':now})
        row['overall_fact_status']='resolved' if all(x in {'consistent','missing'} for x in statuses) else ('conflicting_facts' if 'conflicting_facts' in statuses else 'unknown')
        row['processed_at']=now; resolved.append(row)
    pd.DataFrame(resolved).to_csv(RES,index=False,encoding='utf-8-sig')
    pd.DataFrame(audit).to_csv(AUD,index=False,encoding='utf-8-sig')
    print(pd.DataFrame(resolved)['overall_fact_status'].value_counts(dropna=False).to_dict())

if __name__=='__main__':
    # Deterministic parser assertions act as executable documentation.
    assert parse_datasf_location('0000 3515 FILLMORE            ST0000')['address_from']=='3515'
    assert parse_datasf_location('2053 2051 DIVISADERO          ST0000')['address_from']=='2051'
    assert parse_datasf_location('2053 2051 DIVISADERO          ST0000')['address_to']=='2053'
    main()
