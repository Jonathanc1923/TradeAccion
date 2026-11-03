import urllib.request, ssl, json, time
ctx = ssl._create_unverified_context()
from config import TOPSTEPX_USERNAME, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL

auth_url = f'{TOPSTEPX_BASE_URL}/api/Auth/loginKey'
auth_payload = json.dumps({'userName': TOPSTEPX_USERNAME, 'apiKey': TOPSTEPX_API_KEY}).encode('utf-8')
auth_req = urllib.request.Request(auth_url, data=auth_payload, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(auth_req, context=ctx) as resp:
    token = json.loads(resp.read().decode())['token']

symbols = ['ES', 'NQ', 'YM', 'CL', 'GC']
found = {}

for s in symbols:
    time.sleep(1)
    c_req = urllib.request.Request(f'{TOPSTEPX_BASE_URL}/api/Contract/search',
        data=json.dumps({'searchText': s, 'live': False}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(c_req, context=ctx) as r:
            res = json.loads(r.read().decode())
            contracts = res.get('contracts', [])
            for c in contracts:
                cid = c.get('id', '')
                # Filter futures contracts
                if 'CON.F.US.' in cid:
                    print(f"SYMBOL: {s} | ID: {cid} | Name: {c.get('name')} | TickSize: {c.get('tickSize')} | TickValue: {c.get('tickValue')}")
                    found[s] = c
                    break
    except Exception as e:
        print(f"Error searching {s}: {e}")
