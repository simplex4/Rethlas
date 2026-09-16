"""Immutable bounded file archive, PDF text extraction and readable research exports."""
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import httpx
from .store import inside, page, stamp

MAX_FILE = 20 * 1024 * 1024
SKILLS = Path(__file__).parent / 'skills'

class Library:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS research_files (
                problem_id TEXT NOT NULL, file_id TEXT NOT NULL, name TEXT NOT NULL,
                sha256 TEXT NOT NULL, data BLOB NOT NULL, extracted_text TEXT,
                extraction_status TEXT NOT NULL, provenance TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(problem_id,file_id))''')

    def save(self,pid,fid,name,data,provenance=''):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',fid): raise ValueError('Invalid file_id')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',name) or name in {'.','..'}: raise ValueError('Use a simple filename')
        if not data or len(data)>MAX_FILE or len(provenance)>20000: raise ValueError('File must be 1 byte–20 MB; provenance at most 20000 characters')
        digest=hashlib.sha256(data).hexdigest()
        with self.store.connect() as db:
            self.store.row(db,'problems','problem_id',pid)
            old=db.execute('SELECT sha256,name,provenance FROM research_files WHERE problem_id=? AND file_id=?',(pid,fid)).fetchone()
            if old:
                if tuple(old)!=(digest,name,provenance): raise ValueError('file_id collision; use a new ID')
                return {'file_id':fid,'sha256':digest,'already_saved':True}
        extracted=None; status='binary'
        if name.lower().endswith('.pdf'):
            if not data.startswith(b'%PDF-'): raise ValueError('Invalid PDF header')
            executable=shutil.which('pdftotext')
            status='unavailable: pdftotext not installed'
            if executable:
                with tempfile.TemporaryDirectory() as d:
                    pdf=Path(d)/'input.pdf'; out=Path(d)/'output.txt'; pdf.write_bytes(data)
                    try:
                        result=subprocess.run([executable,'-layout',str(pdf),str(out)],capture_output=True,timeout=45)
                        if result.returncode==0 and out.exists() and out.stat().st_size<=MAX_FILE:
                            extracted=out.read_text(errors='replace'); status='extracted' if extracted.strip() else 'empty: OCR may be required'
                        else: status='failed or extracted text exceeds 20 MB'
                    except subprocess.TimeoutExpired: status='extraction timed out'
        else:
            try: extracted=data.decode('utf-8'); status='utf-8 text'
            except UnicodeDecodeError: pass
        with self.store.connect() as db:
            db.execute('INSERT INTO research_files VALUES (?,?,?,?,?,?,?,?,?)',(pid,fid,name,digest,data,extracted,status,provenance,stamp()))
            self.store.event(db,pid,'save_file',{'file_id':fid,'sha256':digest,'name':name})
        return {'file_id':fid,'sha256':digest,'bytes':len(data),'extraction_status':status}

    def listing(self,pid,offset=0,limit=30):
        if offset<0 or not 1<=limit<=100: raise ValueError('Invalid pagination')
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT file_id,name,sha256,length(data) bytes,extraction_status,provenance FROM research_files WHERE problem_id=? ORDER BY rowid LIMIT ? OFFSET ?',(pid,limit+1,offset))]
        return {'files':rows[:limit],'next_offset':offset+limit if len(rows)>limit else None}

    def read(self,pid,fid,offset=0,limit=30000,encoding='text'):
        with self.store.connect() as db:
            r=db.execute('SELECT * FROM research_files WHERE problem_id=? AND file_id=?',(pid,fid)).fetchone()
        if not r: raise ValueError('Unknown file for problem')
        text=r['extracted_text'] if encoding=='text' else base64.b64encode(r['data']).decode()
        if text is None: raise ValueError('No extracted text; request base64 for original bytes')
        return {'file_id':fid,'file_sha256':r['sha256'],'encoding':encoding,'content':page(text,offset,limit),'extraction_status':r['extraction_status'],'provenance':r['provenance']}

    def download(self,pid,fid,arxiv_id):
        if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?',arxiv_id): raise ValueError('Use an arXiv identifier, not a URL')
        url='https://arxiv.org/pdf/'+arxiv_id
        with httpx.Client(timeout=30,follow_redirects=False,trust_env=False) as client:
            with client.stream('GET',url) as response:
                response.raise_for_status(); chunks=[]; size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>MAX_FILE: raise ValueError('Paper exceeds 20 MB')
                    chunks.append(chunk)
        return self.save(pid,fid,fid+'.pdf',b''.join(chunks),'Downloaded from '+url)

    def export(self,pid):
        directory=inside(self.store.root,self.store.directory/'exports'/(pid.replace('/','_')+'-'+uuid.uuid4().hex))
        directory.mkdir(parents=True)
        with self.store.connect() as db:
            problem=dict(self.store.row(db,'problems','problem_id',pid))
            records=[dict(r) for r in db.execute('SELECT * FROM artifacts WHERE problem_id=? ORDER BY rowid',(pid,))]
            files=[dict(r) for r in db.execute('SELECT * FROM research_files WHERE problem_id=?',(pid,))]
            candidates=[dict(r) for r in db.execute('SELECT * FROM candidates WHERE problem_id=?',(pid,))]
            reviews=[dict(r) for r in db.execute('SELECT r.* FROM reviews r JOIN candidates c ON r.candidate_id=c.candidate_id WHERE c.problem_id=?',(pid,))]
            findings=[dict(r) for r in db.execute('SELECT f.* FROM review_findings f JOIN reviews r ON f.verification_id=r.verification_id JOIN candidates c ON r.candidate_id=c.candidate_id WHERE c.problem_id=?',(pid,))]
        (directory/'problem.json').write_text(json.dumps(problem,ensure_ascii=False,indent=2))
        (directory/'memory.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        (directory/'memory.md').write_text('\n\n'.join('# '+r['record_id']+'\n\nChannel: '+r['channel']+'\n\n'+r['text']+'\n\nProvenance: '+r['provenance'] for r in records))
        (directory/'candidates-and-reviews.json').write_text(json.dumps({'candidates':candidates,'reviews':reviews,'additional_findings':findings},indent=2))
        results=directory/'results'; results.mkdir()
        for candidate in candidates:
            (results/(candidate['candidate_id']+'.md')).write_text(candidate['markdown'])
        for review in reviews:
            (results/(review['verification_id']+'.json')).write_text(json.dumps(review,indent=2))
        for r in files:
            dest=directory/'files'/r['file_id']; dest.mkdir(parents=True)
            (dest/r['name']).write_bytes(r['data'])
            if r['extracted_text'] is not None: (dest/'extracted.txt').write_text(r['extracted_text'])
            (dest/'metadata.json').write_text(json.dumps({k:v for k,v in r.items() if k not in {'data','extracted_text'}},indent=2))
        manifest={str(f.relative_to(directory)):hashlib.sha256(f.read_bytes()).hexdigest() for f in directory.rglob('*') if f.is_file()}
        (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
        return {'directory':str(directory),'records':len(records),'files':len(files),'manifest':str(directory/'manifest.json')}


def list_skills():
    return {'skills':[{'skill_id':p.stem,'description':p.read_text().splitlines()[2]} for p in sorted(SKILLS.glob('*.md'))]}


def read_skill(skill_id,offset=0,limit=30000):
    if not re.fullmatch('[a-z-]+',skill_id): raise ValueError('Invalid skill_id')
    path=SKILLS/(skill_id+'.md')
    if not path.is_file(): raise ValueError('Unknown skill')
    return {'skill_id':skill_id,'content':page(path.read_text(),offset,limit)}
