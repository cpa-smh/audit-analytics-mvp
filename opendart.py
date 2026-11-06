"""Small OpenDART reader: historical accounts first, independent borrowing disclosure context."""
from __future__ import annotations
import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import requests
from xml.etree import ElementTree as ET

from core import decimal, relevant_title


class DartError(RuntimeError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
    def handle_starttag(self, name, attrs):
        if name.lower() in ('tr','p','br','table','section-1','section-2','title'):
            self.fragments.append('\n')
        if name.lower() in ('td','th','tu'):
            self.fragments.append(' | ')
    def handle_endtag(self, name):
        if name.lower() in ('tr','table','p','title','section-1','section-2'):
            self.fragments.append('\n')
    def handle_data(self, data):
        self.fragments.append(data)


def as_text(raw):
    try:
        data = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        data = raw.decode('cp949',errors='replace')
    p = _Text()
    p.feed(data)
    return re.sub(r'[ \t\r]+',' ',''.join(p.fragments))


class Dart:
    def __init__(self, api_key, cache_dir='.cache'):
        self.api_key = api_key
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    def _get(self, endpoint, **params):
        fingerprint = hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
        path = self.cache / (fingerprint+'.bin')
        if path.exists():
            return path.read_bytes()
        if not self.api_key:
            raise DartError('OPENDART_API_KEY가 없습니다.')
        try:
            response = self.session.get('https://opendart.fss.or.kr/api/'+endpoint,
                params={'crtfc_key':self.api_key,**params},timeout=35)
            response.raise_for_status()
            data = response.content
            if endpoint.endswith('.json'):
                parsed = json.loads(data)
                if parsed.get('status') not in ('000','013'):
                    raise DartError('OpenDART 오류: '+str(parsed.get('status')))
            elif not data.startswith(b'PK'):
                raise DartError('OpenDART ZIP 응답이 아닙니다.')
            path.write_bytes(data)
            return data
        except (requests.RequestException,ValueError) as e:
            raise DartError(str(e)) from e

    def _json(self, endpoint, **kwargs):
        return json.loads(self._get(endpoint,**kwargs))

    def company(self, company):
        with zipfile.ZipFile(io.BytesIO(self._get('corpCode.xml'))) as arc:
            root = ET.fromstring(arc.read(arc.namelist()[0]))
        matches=[]
        for node in root.findall('list'):
            name=node.findtext('corp_name','').strip()
            stock=node.findtext('stock_code','').strip()
            if stock and company in (name,stock):
                matches.append((node.findtext('corp_code','').strip(),name))
        if len(matches)!=1:
            raise DartError('상장기업의 정확한 회사명 또는 종목코드를 입력하세요.')
        return matches[0]

    def financials(self, corp_code, year, basis):
        obj=self._json('fnlttSinglAcntAll.json',corp_code=corp_code,
                       bsns_year=str(year),reprt_code='11011',fs_div=basis)
        return obj.get('list',[]) or []

    def audit_note(self, receipt, basis):
        if not receipt:
            return ''
        suffix='_00761.xml' if basis=='CFS' else '_00760.xml'
        with zipfile.ZipFile(io.BytesIO(self._get('document.xml',rcept_no=receipt))) as arc:
            names=[n for n in arc.namelist() if n.endswith(suffix)]
            if not names:
                return ''
            return '\n'.join(as_text(arc.read(n)) for n in names)

    def event_filings(self, corp_code, year, limit=10):
        """Collect independent company context after the calculation, regardless of its threshold."""
        results=[]; page=1
        while True:
            obj=self._json('list.json',corp_code=corp_code,bgn_de=f'{year}0101',
                end_de=f'{year}1231',page_no=page,page_count=100,last_reprt_at='Y')
            for f in obj.get('list',[]) or []:
                if relevant_title(f.get('report_nm','')):
                    results.append(f)
            if page>=int(obj.get('total_page',0) or 0):
                break
            page += 1
        results.sort(key=lambda x:x.get('rcept_no',''),reverse=True)
        return results[:limit],len(results)

    def event_excerpt(self, receipt):
        with zipfile.ZipFile(io.BytesIO(self._get('document.xml',rcept_no=receipt))) as arc:
            names=[n for n in arc.namelist() if n.lower().endswith('.xml')
                   and not re.search(r'_\d{5}\.xml$',n)]
            if not names:
                return '공시 본문 추출 불가'
            text='\n'.join(as_text(arc.read(n)) for n in names[:1])
        lines=[s.strip() for s in text.splitlines() if s.strip()]
        relevant=[]
        for index,line in enumerate(lines):
            if any(w in line for w in ('차입','회사채','발행총액','발행금액','이자율','금리','상환','차환','사채')):
                relevant.append(' '.join(lines[max(0,index-1):min(len(lines),index+2)])[:350])
            if len(relevant)>=5:
                break
        return '\n'.join(relevant)[:1600] if relevant else '관련 금액·금리·기간 근거 미발견'


def receipt_from_rows(rows):
    rs={v.get('rcept_no') for v in rows if v.get('rcept_no')}
    return next(iter(rs)) if len(rs)==1 else ''


def account_values(rows, col):
    """Extract explicit interest expense, avoiding interest PAID and all finance costs."""
    accepted=('이자비용','금융원가중이자비용')
    for statement in ('IS','CIS','CF'):
        items=[r for r in rows if r.get('sj_div')==statement
               and re.sub(r'\s+','',r.get('account_nm','')) in accepted
               and r.get('account_detail','-') in ('','-')
               and r.get('currency')=='KRW']
        if items:
            amounts={decimal(r.get(col)) for r in items}
            if len(amounts)==1 and None not in amounts:
                return next(iter(amounts)),statement+'/'+items[0].get('account_nm','')
            return None,'계정 금액 중복·충돌'
    return None,'해당 계정 미확인'


def note_interest(note):
    """Safely parse one accounting-note section, preserving units and comparative year.
    Return (interest_current, interest_prior, cap_current, cap_prior) in KRW.
    Empty values mean no reliable amount was found.
    """
    matches=list(re.finditer(r'(?m)^\s*\d{1,2}\.\s*금융수익\s*및\s*금융비용\s*$',note or ''))
    if not matches:
        return (None,)*4
    section=note[matches[-1].end():]
    nxt=re.search(r'(?m)^\s*\d{1,2}\.\s*[^\n|]{2,100}$',section)
    if nxt: section=section[:nxt.start()]
    um=re.search(r'단위\s*[:：]\s*(백만원|천원|억원|원)',section)
    if not um:
        return (None,)*4
    multiplier={'원':Decimal(1),'천원':Decimal(1000),
               '백만원':Decimal(1000000),'억원':Decimal(100000000)}[um.group(1)]
    interests=[];caps=[]
    for line in section.splitlines():
        fields=[v.strip() for v in line.strip().strip('|').split('|')]
        if len(fields)<3:continue
        title=re.sub(r'\s+','',fields[0])
        a,b=decimal(fields[1]),decimal(fields[2])
        if a is None or b is None: continue
        if re.fullmatch(r'이자비용(?:\(\*\d+\))?',title):
            interests.append((a*multiplier,b*multiplier))
        if '적격자산원가에포함되어있는금액' in title:
            caps.append((-a*multiplier,-b*multiplier))
    iv=interests[0] if len(set(interests))==1 and interests else (None,None)
    cv=caps[0] if len(set(caps))==1 and caps else (None,None)
    return (*iv,*cv)


def _bs_cell(rows,col,names):
    found=[]
    for r in rows:
        if r.get('sj_div')!='BS' or r.get('currency')!='KRW' or r.get('account_detail','-') not in ('-',''):
            continue
        if re.sub(r'\s+','',r.get('account_nm','')) in names:
            val=decimal(r.get(col))
            if val is not None:
                found.append(val)
    distinct=set(found)
    return next(iter(distinct)) if len(distinct)==1 else None


def debt_balance(rows,col):
    """Avoid mixing current vs non-current subtotals and detailed components."""
    if not rows: return None, '재무제표 자료 없음'
    top_current=_bs_cell(rows,col,('유동성차입금',))
    top_noncurrent=_bs_cell(rows,col,('비유동성차입금',))
    if top_current is not None and top_noncurrent is not None:
        return top_current+top_noncurrent,'유동성차입금+비유동성차입금 (사채·리스 포함 여부 확인 필요)'
    short=_bs_cell(rows,col,('단기차입금',))
    long=_bs_cell(rows,col,('장기차입금',))
    bonds=_bs_cell(rows,col,('사채',))
    lease=_bs_cell(rows,col,('리스부채',))
    cur_lease=_bs_cell(rows,col,('유동성리스부채',))
    cur_long=_bs_cell(rows,col,('유동성장기부채',))
    ab=_bs_cell(rows,col,('자산유동화차입금',))
    if cur_long is not None:
        current_details=[cur_long]
    else:
        current_details=[_bs_cell(rows,col,(key,)) for key in
                         ('유동성장기차입금','유동성사채','유동성자산유동화차입금')]
    if short is None or long is None or bonds is None:
        return None,'필수 차입금 계정(단기·장기·사채)의 구성이 불명확'
    components=[short,long,bonds,lease,cur_lease,ab]+current_details
    return (sum((v for v in components if v is not None),Decimal(0)),
            '단기차입금+장기차입금+사채+확인된 유동성장기부채·리스·유동화차입금')
