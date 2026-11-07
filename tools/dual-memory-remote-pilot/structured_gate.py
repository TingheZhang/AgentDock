#!/usr/bin/env python3
import argparse,hashlib,json,os,pathlib,re,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request,urlopen,HTTPRedirectHandler,build_opener
DEFAULT_ROOT=pathlib.Path('/var/lib/structured-handoff')
FIELDS=('event_id','session_id','title','summary','progress','next_step','acceptance','recipient'); ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$'); MAX=4096
STATE_LOCK=threading.RLock(); AUDIT_LOCK=threading.Lock()
ROOT=DATA=CREDS=LEDGER=AUDIT=BACKEND=WORKSPACE=PROJECT=BIND=None; PORT=None; CLIENTS={}; ROOT_TOKEN=''
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None
def secret(path):
    obj=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj,dict) or not isinstance(obj.get('token'),str) or not obj['token']: raise ValueError('invalid token file')
    return obj['token']
def configure(path=None):
    global ROOT,DATA,CREDS,LEDGER,AUDIT,BACKEND,WORKSPACE,PROJECT,BIND,PORT,CLIENTS,ROOT_TOKEN
    if path is None:
        root=DEFAULT_ROOT; spec={'root':str(root),'data':str(root/'data'),'credentials':str(root/'credentials'),'ledger':str(root/'gate-ledger.json'),'audit':str(root/'logs/gate-audit.jsonl'),'backend_url':'http://127.0.0.1:18080/mcp','bind':'127.0.0.1','port':18081,'workspace':'structured-handoff','project':'handoffs','root_token_file':str(root/'data/auth-tokens/default'),'clients':[{'owner':n,'token_file':str(root/'credentials'/(n+'.json'))} for n in ('client-a','client-b')]}
    else:
        spec=path if isinstance(path,dict) else json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
        required=('root','data','credentials','ledger','audit','backend_url','bind','port','workspace','project','root_token_file','clients')
        if not isinstance(spec,dict) or any(k not in spec for k in required): raise ValueError('config missing required fields')
    if spec['bind']!='127.0.0.1' or not isinstance(spec['port'],int) or not 1024<=spec['port']<=65535: raise ValueError('IPv4 loopback bind/port required')
    u=urlparse(spec['backend_url']);
    if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.query or u.fragment or u.path!='/mcp' or u.port is None or not 1<=u.port<=65535: raise ValueError('backend_url must be numeric IPv4 loopback http /mcp')
    clients={}; seen=set()
    if not isinstance(spec['clients'],list) or not spec['clients']: raise ValueError('clients required')
    for item in spec['clients']:
        if not isinstance(item,dict) or not ID.fullmatch(item.get('owner','')) or item['owner'] in seen: raise ValueError('duplicate/invalid owner')
        p=pathlib.Path(item.get('token_file',''))
        if not p.is_file(): raise ValueError('client token file missing')
        token=secret(p)
        if token in clients: raise ValueError('duplicate client token')
        clients[token]=item['owner']; seen.add(item['owner'])
    root_token_file=pathlib.Path(spec['root_token_file'])
    if not root_token_file.is_file(): raise ValueError('root token file missing')
    root_token=root_token_file.read_text(encoding='utf-8').strip()
    if not root_token: raise ValueError('empty root token')
    for k in ('root','data','credentials'):
        if not pathlib.Path(spec[k]).is_dir(): raise ValueError(f'{k} directory missing')
    for k in ('ledger','audit'):
        if not pathlib.Path(spec[k]).parent.is_dir(): raise ValueError(f'{k} parent missing')
    ROOT=pathlib.Path(spec['root']); DATA=pathlib.Path(spec['data']); CREDS=pathlib.Path(spec['credentials']); LEDGER=pathlib.Path(spec['ledger']); AUDIT=pathlib.Path(spec['audit']); BACKEND=spec['backend_url']; WORKSPACE=spec['workspace']; PROJECT=spec['project']; BIND=spec['bind']; PORT=spec['port']; CLIENTS=clients; ROOT_TOKEN=root_token
def load_ledger():
    try:return json.loads(LEDGER.read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
def save_ledger(value):
    fd,tmp=tempfile.mkstemp(prefix='.gate-',dir=str(LEDGER.parent),text=True)
    with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,LEDGER)
def backend(method,args):
    args=dict(args); name=args.pop('_name'); body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':{'name':name,'arguments':args}}).encode()
    req=Request(BACKEND,data=body,method='POST',headers={'Authorization':'Bearer '+ROOT_TOKEN,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    with build_opener(NoRedirect).open(req,timeout=15) as r: result=json.loads(r.read(65536))
    if 'error' in result: raise RuntimeError('backend rpc error')
    env=result.get('result') or {}
    if env.get('isError'): raise RuntimeError('backend tool error')
    content=env.get('content') or []
    if not content or not isinstance(content[0],dict) or not isinstance(content[0].get('text'),str): raise RuntimeError('backend malformed result')
    try:value=json.loads(content[0]['text'])
    except Exception: value={'text':content[0]['text']}
    if isinstance(value,dict) and value.get('error'): raise RuntimeError('backend structured error')
    return value
def audit(row):
    with AUDIT_LOCK:
        with AUDIT.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
def reject(h,code,msg):
    raw=json.dumps({'error':msg},ensure_ascii=False).encode(); h.send_response(code); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(raw))); h.end_headers(); h.wfile.write(raw)
class Gate(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def auth(self):
        v=self.headers.get('Authorization',''); return CLIENTS.get(v[7:]) if v.startswith('Bearer ') else None
    def body(self):
        n=int(self.headers.get('Content-Length','-1'))
        if n<0 or n>MAX:raise ValueError('body too large or missing length')
        obj=json.loads(self.rfile.read(n))
        if not isinstance(obj,dict) or set(obj)!=set(FIELDS):raise ValueError('exact structured handoff fields required')
        if len(json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode())>MAX:raise ValueError('body too large')
        if any(not isinstance(obj[k],str) or not obj[k] or len(obj[k])>512 for k in FIELDS):raise ValueError('bounded string fields required')
        if not ID.fullmatch(obj['event_id']) or not ID.fullmatch(obj['session_id']):raise ValueError('invalid event/session id')
        return obj
    def send_json(self,code,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        if self.path!='/handoff':return reject(self,404,'only /handoff is exposed')
        owner=self.auth()
        if not owner:return reject(self,401,'client credential required')
        try:obj=self.body()
        except Exception as e:return reject(self,400,str(e))
        received=time.time_ns(); request_id=obj['event_id']; digest=hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with STATE_LOCK:
            old=load_ledger().get(obj['event_id'])
            if old:
                if old['owner']!=owner:return reject(self,409,'event_id owner conflict')
                if old['digest']!=digest:return reject(self,409,'event_id payload conflict')
                return self.send_json(200,{'status':'duplicate','event_id':obj['event_id'],'owner':owner,'page_path':old['page_path'],'page_id':old['page_id']})
            path=f'handoffs/{obj["event_id"]}.md'; body='# '+obj['title']+'\n\n摘要：'+obj['summary']+'\n进度：'+obj['progress']+'\n下一步：'+obj['next_step']+'\n验收：'+obj['acceptance']+'\n发送者：'+owner+'\n接收者：'+obj['recipient']+'\n事件：'+obj['event_id']+'\n会话：'+obj['session_id']
            try:page=backend('tools/call',{'_name':'memory_write_page','path':path,'body':body,'kind':'fact','tier':'episodic','workspace':WORKSPACE,'project':PROJECT,'create_only':True}); page_id=page.get('page_id')
            except Exception:return reject(self,502,'backend write failed')
            if not isinstance(page_id,str) or not page_id:return reject(self,502,'backend write missing page_id')
            ledger=load_ledger(); ledger[obj['event_id']]={'digest':digest,'owner':owner,'page_path':path,'page_id':page_id}; save_ledger(ledger); completed=time.time_ns(); audit({'request_id':request_id,'event_id':obj['event_id'],'session_id':obj['session_id'],'owner':owner,'received_ns':received,'completed_ns':completed,'status':'stored'})
            self.send_json(201,{'status':'stored','event_id':obj['event_id'],'owner':owner,'page_path':path,'page_id':page_id})
    def do_GET(self):
        if not self.path.startswith('/handoff?event_id='):return reject(self,404,'only structured handoff reads are exposed')
        owner=self.auth()
        if not owner:return reject(self,401,'client credential required')
        event=self.path.split('=',1)[1]
        if not ID.fullmatch(event):return reject(self,400,'invalid event id')
        with STATE_LOCK:row=load_ledger().get(event)
        if not row:return reject(self,404,'event not found')
        try:page=backend('tools/call',{'_name':'memory_read_page','path':row['page_path'],'workspace':WORKSPACE,'project':PROJECT})
        except Exception:return reject(self,502,'backend read failed')
        self.send_json(200,{'status':'read','event_id':event,'owner':row['owner'],'page_path':row['page_path'],'page_id':page.get('page_id'),'body':page.get('body')})
def main():
    p=argparse.ArgumentParser(); p.add_argument('--config'); a=p.parse_args(); configure(a.config); ThreadingHTTPServer((BIND,PORT),Gate).serve_forever()
if __name__=='__main__':main()
