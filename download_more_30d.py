import urllib.request, ssl, json, time, os

ctx = ssl._create_unverified_context()
from config import TOPSTEPX_USERNAME, TOPSTEPX_API_KEY, TOPSTEPX_BASE_URL

def fetch_bars(contract_id, label, tok):
    path = f"cache_m30_30d_{label}.json"
    if os.path.exists(path):
        return
    url = f"{TOPSTEPX_BASE_URL}/api/History/retrieveBars"
    payload = {
        "contractId": contract_id, "live": False,
        "startTime": "2026-10-04T00:00:00Z", "endTime": "2026-11-03T23:59:59Z",
        "unit": 2, "unitNumber": 30, "limit": 20000, "includePartialBar": False
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=25) as r:
            data = json.loads(r.read().decode())
            bars = data.get("bars", [])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bars, f)
            print(f"Downloaded {len(bars)} for {label}")
    except Exception as e:
        print(f"Error for {label}: {e}")

if __name__ == "__main__":
    time.sleep(10)
    auth_url = f"{TOPSTEPX_BASE_URL}/api/Auth/loginKey"
    auth_payload = json.dumps({"userName": TOPSTEPX_USERNAME, "apiKey": TOPSTEPX_API_KEY}).encode("utf-8")
    auth_req = urllib.request.Request(auth_url, data=auth_payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(auth_req, context=ctx, timeout=15) as resp:
        tok = json.loads(resp.read().decode())["token"]
    
    fetch_bars("CON.F.US.MYM.Z26", "YM", tok)
    time.sleep(3)
    fetch_bars("CON.F.US.CLE.Z26", "CL", tok)
    time.sleep(3)
    fetch_bars("CON.F.US.GCE.Z26", "GC", tok)
