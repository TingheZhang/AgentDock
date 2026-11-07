#!/usr/bin/env python3
"""Small durable structured-handoff outbox client.

The token is read from a separate owner-only file and is never stored in the
SQLite database or logs.  This is a single-worker client, not a service.
"""
import argparse, hashlib, json, re, sqlite3, sys, time, http.client
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

FIELDS=("event_id","session_id","title","summary","progress","next_step","acceptance","recipient")
ID_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
MAX=512
RETRY={408,429,500,502,503,504}

def utc(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def digest(payload): return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def read_token(path):
    p=Path(path); raw=p.read_text(encoding="utf-8").strip()
    try:
        obj=json.loads(raw); token=obj.get("token","") if isinstance(obj,dict) else ""
    except json.JSONDecodeError: token=raw
    if not token or len(token)>512 or any(c.isspace() for c in token): raise ValueError("invalid token file")
    return token
def validate(payload):
    if not isinstance(payload,dict) or len(json.dumps(payload,ensure_ascii=False,separators=(",",":" )).encode("utf-8"))>4096: raise ValueError("payload must be an object <=4096 UTF-8 bytes")
    if set(payload)!=set(FIELDS): raise ValueError("payload must contain exactly the eight structured fields")
    for key in FIELDS:
        val=payload[key]
        if not isinstance(val,str) or not val or len(val)>MAX: raise ValueError(f"invalid {key}")
        if key in ("event_id","session_id") and not ID_RE.fullmatch(val): raise ValueError(f"invalid {key}")
    return payload
def endpoint_ok(endpoint):
    u=urlparse(endpoint)
    if u.scheme not in ("http","https") or not u.netloc or u.query or u.fragment: raise ValueError("endpoint must be an explicit http(s) URL")
    return endpoint.rstrip("/")
def db(path,owner=None,endpoint=None):
    c=sqlite3.connect(path); c.row_factory=sqlite3.Row
    c.executescript("""CREATE TABLE IF NOT EXISTS jobs(
      event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL,
      digest TEXT NOT NULL, owner TEXT NOT NULL, endpoint TEXT NOT NULL,
      state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      next_at REAL NOT NULL DEFAULT 0, last_error TEXT, receipt TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,next_at);""")
    c.execute("CREATE TABLE IF NOT EXISTS binding(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    if owner is not None:
        endpoint=endpoint_ok(endpoint); rows=dict(c.execute("SELECT key,value FROM binding").fetchall())
        if rows and (rows.get("owner")!=owner or rows.get("endpoint")!=endpoint): raise ValueError("outbox owner/endpoint binding mismatch")
        if not rows: c.executemany("INSERT INTO binding(key,value) VALUES(?,?)",[("owner",owner),("endpoint",endpoint)]); c.commit()
    return c
def enqueue(args):
    payload=validate(json.loads(Path(args.payload).read_text(encoding="utf-8"))); d=digest(payload); now=utc()
    c=db(args.db,args.owner,args.endpoint); row=c.execute("SELECT * FROM jobs WHERE event_id=?",(payload["event_id"],)).fetchone()
    if row:
        if row["owner"]!=args.owner: raise SystemExit("event_id owner conflict")
        if row["digest"]!=d: raise SystemExit("event_id payload conflict")
        print(json.dumps({"status":"duplicate","event_id":row["event_id"],"state":row["state"]})); return
    c.execute("INSERT INTO jobs(event_id,session_id,payload,digest,owner,endpoint,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (payload["event_id"],payload["session_id"],json.dumps(payload,ensure_ascii=False,separators=(",",":")),d,args.owner,args.endpoint,"pending",now,now)); c.commit()
    print(json.dumps({"status":"queued","event_id":payload["event_id"],"digest":d}))
def post(args,row,token):
    u=urlparse(row["endpoint"]); conn=(http.client.HTTPSConnection if u.scheme=="https" else http.client.HTTPConnection)(u.hostname,u.port,timeout=args.timeout)
    try:
        conn.request("POST",(u.path.rstrip("/") or "")+"/handoff",body=row["payload"].encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"}); r=conn.getresponse(); code=r.status; body=r.read(4096)
    except (TimeoutError,OSError) as e: raise RuntimeError(f"transport:{type(e).__name__}") from e
    finally: conn.close()
    try: result=json.loads(body.decode("utf-8"))
    except Exception: result=None
    if code in RETRY: raise RuntimeError(f"retryable_http:{code}")
    if code in (400,401,403,409) or 400<=code<500: return ("terminal",{"http":code})
    if code<200 or code>=300: raise RuntimeError(f"http:{code}")
    expected=(code==201 and isinstance(result,dict) and result.get("status")=="stored") or (code==200 and isinstance(result,dict) and result.get("status")=="duplicate")
    if not expected or result.get("event_id")!=row["event_id"] or result.get("owner")!=row["owner"] or not isinstance(result.get("page_id"),str) or not result.get("page_id"):
        raise RuntimeError("invalid_ack")
    return ("acked",result)
def run(args):
    token=read_token(args.token); deadline=time.monotonic()+args.deadline; c=db(args.db,args.owner,args.endpoint); sent=0
    while time.monotonic()<deadline:
        row=c.execute("SELECT * FROM jobs WHERE state='pending' AND next_at<=? ORDER BY created_at LIMIT 1",(time.time(),)).fetchone()
        if not row:
            if args.once: break
            time.sleep(min(args.poll, max(0,deadline-time.monotonic()))); continue
        c.execute("UPDATE jobs SET attempts=attempts+1,updated_at=? WHERE event_id=? AND state='pending'",(utc(),row["event_id"])); c.commit()
        row=c.execute("SELECT * FROM jobs WHERE event_id=?",(row["event_id"],)).fetchone()
        try: state,receipt=post(args,row,token)
        except RuntimeError as e:
            delay=min(args.max_backoff,args.backoff*(2**min(row["attempts"],8))); c.execute("UPDATE jobs SET next_at=?,last_error=?,updated_at=? WHERE event_id=?",(time.time()+delay,str(e),utc(),row["event_id"])); c.commit(); sent+=1; continue
        if state=="acked": c.execute("UPDATE jobs SET state='acked',receipt=?,last_error=NULL,updated_at=? WHERE event_id=?",(json.dumps(receipt,separators=(",",":")),utc(),row["event_id"]))
        else: c.execute("UPDATE jobs SET state='terminal',receipt=?,last_error=?,updated_at=? WHERE event_id=?",(json.dumps(receipt,separators=(",",":")),f"terminal_http:{receipt.get('http')}",utc(),row["event_id"]))
        c.commit(); sent+=1
    print(json.dumps({"status":"stopped","processed":sent}))
def status(args):
    c=db(args.db); print(json.dumps([dict(r) for r in c.execute("SELECT event_id,session_id,digest,owner,endpoint,state,attempts,next_at,last_error,receipt FROM jobs ORDER BY created_at")],ensure_ascii=False))
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    def common(x): x.add_argument("--db",required=True); x.add_argument("--owner",required=True); x.add_argument("--endpoint",required=True)
    x=sub.add_parser("enqueue"); common(x); x.add_argument("--payload",required=True); x.set_defaults(fn=enqueue)
    x=sub.add_parser("run"); common(x); x.add_argument("--token",required=True); x.add_argument("--deadline",type=float,default=30); x.add_argument("--poll",type=float,default=1); x.add_argument("--backoff",type=float,default=.5); x.add_argument("--max-backoff",type=float,default=30); x.add_argument("--timeout",type=float,default=5); x.add_argument("--once",action="store_true"); x.set_defaults(fn=run)
    x=sub.add_parser("status"); x.add_argument("--db",required=True); x.set_defaults(fn=status)
    a=p.parse_args(); a.fn(a)
if __name__=="__main__": main()
