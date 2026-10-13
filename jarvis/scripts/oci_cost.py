import json,subprocess,os,datetime
os.environ['OCI_CLI_CONFIG_FILE']=r'D:\jarvis-secrets\oci\config'
os.environ['OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING']='True'
k=json.load(open(r'D:\jarvis-secrets\keys.json',encoding='utf-8-sig'))['oci']
OCI=r'D:\jarvis\tools\oci-venv\Scripts\oci.exe'
today=datetime.date.today(); start=today.replace(day=1); end=today+datetime.timedelta(days=1)
r=subprocess.run([OCI,'usage-api','usage-summary','request-summarized-usages','--tenant-id',k['tenancy_ocid'],
  '--time-usage-started',start.isoformat()+'T00:00:00Z','--time-usage-ended',end.isoformat()+'T00:00:00Z','--granularity','MONTHLY',
  '--group-by','["service"]','--query-type','COST','--output','json'],capture_output=True,text=True)
if r.returncode: print('usage-api error:',(r.stderr.strip().splitlines() or ['?'])[-1][:250]); raise SystemExit
items=(json.loads(r.stdout) if r.stdout.strip() else {}).get('data',{}).get('items',[])
print('period',start,'to',today,'| line items:',len(items))
tot=0.0
for i in items:
    c=i.get('computed-amount') or 0; tot+=c
    print(' ',i.get('service'),'cost',c,i.get('currency'))
print('TOTAL cost this month:',round(tot,4))
