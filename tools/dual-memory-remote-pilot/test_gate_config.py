import importlib.util,json,tempfile,unittest,threading,time
from urllib.request import Request,urlopen
from urllib.request import ProxyHandler
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
P=Path(__file__).with_name('structured_gate.py'); spec=importlib.util.spec_from_file_location('gate',P); gate=importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
class T(unittest.TestCase):
  def test_explicit_config_isolated_and_duplicate_rejected(self):
    root=Path(tempfile.mkdtemp()); data=root/'data'; creds=root/'creds'; (data/'auth-tokens').mkdir(parents=True); creds.mkdir(); (root/'logs').mkdir()
    (data/'auth-tokens'/'default').write_text('root-secret');
    for n,t in [('client-a','a-secret'),('client-b','b-secret')]: (creds/(n+'.json')).write_text(json.dumps({'token':t}))
    cfg={'root':str(root),'data':str(data),'credentials':str(creds),'ledger':str(root/'logs/l.json'),'audit':str(root/'logs/a.jsonl'),'backend_url':'http://127.0.0.1:18080/mcp','bind':'127.0.0.1','port':18081,'workspace':'w','project':'p','root_token_file':str(data/'auth-tokens/default'),'clients':[{'owner':'client-a','token_file':str(creds/'client-a.json')},{'owner':'client-b','token_file':str(creds/'client-b.json')}]}; cp=root/'c.json'; cp.write_text(json.dumps(cfg)); gate.configure(cp); self.assertEqual(gate.WORKSPACE,'w'); self.assertEqual(gate.PROJECT,'p'); self.assertEqual(len(gate.CLIENTS),2)
    
  def test_duplicate_owner_fails_closed(self):
    with self.assertRaises(ValueError): gate.configure({'x':1})
  def test_backend_headers_and_create_only(self):
    got={}
    class H(BaseHTTPRequestHandler):
      def do_POST(self):
        got['accept']=self.headers.get('Accept'); got['body']=json.loads(self.rfile.read(int(self.headers['Content-Length']))); raw=json.dumps({'result':{'content':[{'text':json.dumps({'page_id':'p1'})}]}}).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
      def log_message(self,*a): pass
    s=HTTPServer(('127.0.0.1',0),H); threading.Thread(target=s.serve_forever,daemon=True).start(); gate.BACKEND=f'http://127.0.0.1:{s.server_port}/mcp'; gate.ROOT_TOKEN='synthetic'; old=gate.build_opener; seen=[]; gate.build_opener=lambda *handlers: (seen.extend(handlers) or old(*handlers)); out=gate.backend('tools/call',{'_name':'memory_write_page','create_only':True}); self.assertEqual(out['page_id'],'p1'); self.assertIn('application/json',got['accept']); self.assertTrue(got['body']['params']['arguments']['create_only']); self.assertTrue(any(isinstance(h,ProxyHandler) and h.proxies=={} for h in seen)); gate.build_opener=old; s.shutdown()
  def test_unsafe_backend_urls_fail_closed(self):
    root=Path(tempfile.mkdtemp()); data=root/'data'; creds=root/'creds'; (data/'auth-tokens').mkdir(parents=True); creds.mkdir(); (root/'logs').mkdir(); (data/'auth-tokens'/'default').write_text('r'); (creds/'client-a.json').write_text('{"token":"a"}')
    base={'root':str(root),'data':str(data),'credentials':str(creds),'ledger':str(root/'logs/l'),'audit':str(root/'logs/a'),'backend_url':'http://127.0.0.1:1/mcp','bind':'127.0.0.1','port':18081,'workspace':'w','project':'p','root_token_file':str(data/'auth-tokens/default'),'clients':[{'owner':'client-a','token_file':str(creds/'client-a.json')}]}
    for bad in ('http://localhost:1/mcp','http://127.0.0.1:1/mcp?x=1','http://user:pw@127.0.0.1:1/mcp','https://127.0.0.1:1/mcp','http://127.0.0.1:1/redirect'):
      with self.subTest(bad=bad):
        cfg=dict(base); cfg['backend_url']=bad
        with self.assertRaises(ValueError): gate.configure(cfg)
    cfg=dict(base); cfg['bind']='::1'
    with self.assertRaises(ValueError): gate.configure(cfg)
  def test_audit_contract_ordered_timestamps(self):
    root=Path(tempfile.mkdtemp()); gate.AUDIT=root/'audit.jsonl'; gate.audit({'request_id':'e1','event_id':'e1','session_id':'s1','owner':'client-a','received_ns':10,'completed_ns':20,'status':'stored'})
    row=json.loads(gate.AUDIT.read_text()); self.assertEqual(set(row),{'request_id','event_id','session_id','owner','received_ns','completed_ns','status'}); self.assertLessEqual(row['received_ns'],row['completed_ns'])
  def test_audit_received_precedes_delayed_backend(self):
    root=Path(tempfile.mkdtemp()); (root/'logs').mkdir(); gate.ROOT=root; gate.LEDGER=root/'ledger.json'; gate.AUDIT=root/'logs/audit.jsonl'; gate.CLIENTS={'tok':'client-a'}; gate.WORKSPACE='w'; gate.PROJECT='p'; marks=[]
    old=gate.backend
    def fake(*a,**k): marks.append(time.time_ns()); time.sleep(.03); return {'page_id':'p1'}
    gate.backend=fake; s=HTTPServer(('127.0.0.1',0),gate.Gate); threading.Thread(target=s.serve_forever,daemon=True).start(); payload={k:('evt-delay' if k=='event_id' else ('ses-delay' if k=='session_id' else 'v')) for k in gate.FIELDS}; req=Request(f'http://127.0.0.1:{s.server_port}/handoff',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer tok','Content-Type':'application/json'}); self.assertEqual(urlopen(req).status,201); row=json.loads(gate.AUDIT.read_text()); self.assertLessEqual(row['received_ns'],marks[0]); self.assertLessEqual(marks[0],row['completed_ns']); s.shutdown(); gate.backend=old
  def test_receiver_reads_sender_by_event_id(self):
    root=Path(tempfile.mkdtemp()); gate.ROOT=root; gate.LEDGER=root/'ledger.json'; gate.AUDIT=root/'audit.jsonl'; gate.CLIENTS={'send-token':'sender','receive-token':'receiver'}; gate.WORKSPACE='w'; gate.PROJECT='p'
    gate.backend=lambda name,args: {'page_id':'page-evt-cross','body':'摘要：中文交接'} if args.get('_name')=='memory_read_page' else {'page_id':'page-evt-cross'}
    s=HTTPServer(('127.0.0.1',0),gate.Gate); threading.Thread(target=s.serve_forever,daemon=True).start(); payload={k:('evt-cross' if k=='event_id' else ('sess-cross' if k=='session_id' else '中文')) for k in gate.FIELDS}; req=Request(f'http://127.0.0.1:{s.server_port}/handoff',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer send-token','Content-Type':'application/json'}); self.assertEqual(urlopen(req).status,201); read=Request(f'http://127.0.0.1:{s.server_port}/handoff?event_id=evt-cross',headers={'Authorization':'Bearer receive-token'}); result=json.loads(urlopen(read).read()); self.assertEqual(result['owner'],'sender'); self.assertEqual(result['event_id'],'evt-cross'); self.assertIn('中文交接',result['body']); s.shutdown()
if __name__=='__main__': unittest.main()
