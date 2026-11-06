"""OpenDART collection; cached responses never contain the API key."""
import hashlib,json,re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from html.parser import HTMLParser
from defusedxml import ElementTree as ET
import requests

class DartError(ValueError): pass

class TextReader(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self,data): self.parts.append(data)
    def handle_endtag(self,tag):
        if tag.lower() in ('tr','p','title','table','section-1','section-2'): self.parts.append('\n')
        elif tag.lower() in ('td','th','tu'): self.parts.append(' | ')

def plain(raw):
    try: text=raw.decode('utf-8-sig')
    except UnicodeDecodeError: text=raw.decode('cp949')
    reader=TextReader(); reader.feed(text)
    return re.sub(r'[ \t\r]+',' ',''.join(reader.parts))

class Dart:
    def __init__(self,key,cache):
        self.key=key; self.cache=Path(cache); self.cache.mkdir(parents=True,exist_ok=True)
    def get(self,endpoint,**params):
        digest=hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
        path=self.cache/(digest+'.bin')
        if path.exists(): return path.read_bytes()
        if not self.key: raise DartError('OPENDART_API_KEY 없음')
        try:
            r=requests.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':self.key,**params},timeout=45)
            if r.status_code!=200: raise DartError(f'DART HTTP {r.status_code}')
        except requests.RequestException: raise DartError('DART 통신 실패') from None
        raw=r.content
        if endpoint.endswith('.json'):
            data=json.loads(raw)
            if data.get('status')=='013': return raw
            if data.get('status')!='000': raise DartError('DART 응답 코드 '+str(data.get('status')))
        elif not raw.startswith(b'PK'): raise DartError('DART ZIP 수신 실패: 키·접수번호 확인')
        path.write_bytes(raw);return raw
    def json(self,endpoint,**params): return json.loads(self.get(endpoint,**params))
    def company(self,name):
        with ZipFile(BytesIO(self.get('corpCode.xml'))) as z: root=ET.fromstring(z.read(z.namelist()[0]))
        hits=[{c.tag:c.text or '' for c in row} for row in root.findall('list') if row.findtext('corp_name')==name or row.findtext('stock_code')==name]
        hits=[x for x in hits if x.get('stock_code','').strip()]
        if len(hits)!=1: raise DartError('상장기업을 정확한 회사명 또는 종목코드로 지정하세요.')
        p=self.json('company.json',corp_code=hits[0]['corp_code'])
        if p.get('acc_mt')!='12': raise DartError('MVP는 12월 결산기업만 지원합니다.')
        return hits[0]
    def financials(self,code,year,basis):
        return self.json('fnlttSinglAcntAll.json',corp_code=code,bsns_year=str(year),reprt_code='11011',fs_div=basis).get('list',[])
    def filings(self,code,start,end):
        rows=[];page=1
        while True:
            d=self.json('list.json',corp_code=code,bgn_de=start,end_de=end,last_reprt_at='Y',page_no=page,page_count=100)
            rows.extend(d.get('list',[]))
            if page>=int(d.get('total_page',0)):return rows
            page+=1
    def document(self,receipt,basis,annual=True):
        with ZipFile(BytesIO(self.get('document.xml',rcept_no=receipt))) as z:
            suffix='_00761.xml' if basis=='CFS' else '_00760.xml'
            names=[n for n in z.namelist() if n.endswith(suffix)] if annual else [n for n in z.namelist() if n.endswith('.xml') and not re.search(r'_\d{5}\.xml$',n)]
            if not names: raise DartError('해당 연결/별도 감사보고서 첨부 없음; 혼용 방지를 위해 주석 생략')
            return '\n'.join(plain(z.read(n)) for n in names)

KEYWORDS={'이자비용':('이자비용','차입','사채','금리','상환','차환')}
def excerpts(text,account,limit=8):
    """Merge adjacent windows, retain source table headers, bound one-call input."""
    windows=[]
    for m in re.finditer('|'.join(KEYWORDS[account]),text):
        lo=max(0,m.start()-500);hi=min(len(text),m.end()+1800)
        if windows and lo<=windows[-1][1]: windows[-1][1]=min(windows[-1][0]+5000,max(hi,windows[-1][1]))
        else: windows.append([lo,hi])
    return [text[a:b] for a,b in windows[:limit]]

def number(value):
    if value is None:return None
    s=str(value).strip().replace(',','')
    if not re.fullmatch(r'\(?-?\d+(?:\.\d+)?\)?',s):return None
    from decimal import Decimal
    return Decimal('-'+s[1:-1] if s.startswith('(') else s)

def expense(rows,account):
    """Use recognized interest expense, not paid interest or total finance costs."""
    names={'이자비용','금융원가중이자비용'}
    if account!='이자비용':raise ValueError('이자비용 계정만 지원합니다.')
    candidates=[r for r in rows if re.sub(r'\s+','',r.get('account_nm','')) in names and r.get('sj_div') in ('IS','CIS','CF') and r.get('currency')=='KRW' and r.get('account_detail','-') in ('-','')]
    for sj in ('IS','CIS','CF'):
        selected=[r for r in candidates if r['sj_div']==sj]
        amounts={number(r.get('thstrm_amount')) for r in selected}
        if selected:
            if len(amounts)!=1 or None in amounts:return None,None
            value=next(iter(amounts))
            if value<0:return None,None
            return value,selected[0]
    return None,None
