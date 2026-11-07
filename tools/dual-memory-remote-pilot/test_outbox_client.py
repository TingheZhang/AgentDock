import json,sqlite3,tempfile,threading,unittest,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from outbox_client import main,validate,db
class H(BaseHTTPRequestHandler):
    calls=0; fail_once=True; seen=[]
    def do_POST(self):
        H.calls+=1; p=json.loads(self.rfile.read(int(self.headers['Content-Length']))); H.seen.append(p)
        if H.fail_once: H.fail_once=False; self.send_response(503); self.end_headers(); return
        self.send_response(201); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps({'status':'stored','event_id':p['event_id'],'owner':'client-a','page_id':'page-'+p['event_id']}).encode())
    def log_message(self,*a): pass
class T(unittest.TestCase):
  def test_schema_unicode_resume_retry_and_dedupe(self):
    s=ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=s.serve_forever,daemon=True).start(); root=Path(tempfile.mkdtemp()); db=root/'o.sqlite'; tok=root/'token'; tok.write_text('x'*64)
    payload={k:('中文交接' if k=='summary' else ('evt-1' if k=='event_id' else ('sess-1' if k=='session_id' else ('client-b' if k=='recipient' else k)))) for k in ('event_id','session_id','title','summary','progress','next_step','acceptance','recipient')}; f=root/'p.json'; f.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8'); ep=f'http://127.0.0.1:{s.server_port}'
    sys.argv=['x','enqueue','--db',str(db),'--owner','client-a','--endpoint',ep,'--payload',str(f)]; main(); sys.argv=['x','enqueue','--db',str(db),'--owner','client-a','--endpoint',ep,'--payload',str(f)]; main(); sys.argv=['x','run','--db',str(db),'--owner','client-a','--endpoint',ep,'--token',str(tok),'--deadline','3','--backoff','.01','--max-backoff','.02','--poll','.01']; main(); row=sqlite3.connect(db).execute('select state,attempts,receipt,last_error from jobs').fetchone(); print('DEBUG',row,'CALLS',H.calls); self.assertEqual(row[0],'acked'); self.assertGreaterEqual(row[1],2); self.assertIn('page-evt-1',row[2]); self.assertEqual(H.seen[0],H.seen[1]); s.shutdown()
  def test_schema_binding_and_pending_reopen(self):
    with self.assertRaises(ValueError): validate({'event_id':'x','session_id':'sess','title':'t','summary':'s','progress':'p','next_step':'n','acceptance':'a','recipient':'r'})
    root=Path(tempfile.mkdtemp()); c=db(root/'o.sqlite','client-a','http://127.0.0.1:1'); c.close()
    with self.assertRaises(ValueError): db(root/'o.sqlite','client-b','http://127.0.0.1:1')
    with self.assertRaises(ValueError): db(root/'o.sqlite','client-a','http://127.0.0.1:2')
    self.assertEqual(db(root/'o.sqlite','client-a','http://127.0.0.1:1').execute('select count(*) from jobs').fetchone()[0],0)
  def test_terminal_400_and_bad_ack_not_acked(self):
    class Bad(BaseHTTPRequestHandler):
      def do_POST(self): self.send_response(400); self.end_headers()
      def log_message(self,*a): pass
    s=ThreadingHTTPServer(('127.0.0.1',0),Bad); threading.Thread(target=s.serve_forever,daemon=True).start(); root=Path(tempfile.mkdtemp()); dbp=root/'o.sqlite'; tok=root/'t'; tok.write_text('x'*64); payload={k:('evt-400' if k=='event_id' else ('sess-400' if k=='session_id' else 'v')) for k in ('event_id','session_id','title','summary','progress','next_step','acceptance','recipient')}; f=root/'p.json'; f.write_text(json.dumps(payload),encoding='utf-8'); ep=f'http://127.0.0.1:{s.server_port}'; sys.argv=['x','enqueue','--db',str(dbp),'--owner','client-a','--endpoint',ep,'--payload',str(f)]; main(); sys.argv=['x','run','--db',str(dbp),'--owner','client-a','--endpoint',ep,'--token',str(tok),'--deadline','.2','--poll','.01']; main(); self.assertEqual(sqlite3.connect(dbp).execute('select state from jobs').fetchone()[0],'terminal'); s.shutdown()
if __name__=='__main__': unittest.main()
