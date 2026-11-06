"""Interest expense analytical procedures MVP (Python 3.10+)."""
import argparse,json,os,re
from datetime import date
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
from dart import Dart,DartError,expense,excerpts,number
from ai import ask,citation,input_value,calculate
from overall import overall,formatted_rows

ACCOUNTS=('이자비용',)
PROCEDURES={'이자비용':'차입금 명세·은행조회서·계약서 대사, 일별 잔액×약정금리 재계산, 미지급이자 기간귀속 및 자본화 검토'}
def change(current,previous,pct,amount):
    if current is None or previous is None:return None,None,False,'자료 없음'
    delta=current-previous;rate=delta/abs(previous) if previous else None
    flag=abs(delta)>=amount and (abs(rate)>=pct if rate is not None else current!=0)
    return delta,rate,flag,'전기 0: 증감률 미정의, 금액 기준으로 탐색' if previous==0 else '유의적 변동' if flag else '설정 기준 미만'

def collect(args):
    client=Dart(os.getenv('OPENDART_API_KEY',''),args.cache);company=client.company(args.company)
    values={a:{} for a in ACCOUNTS};sources=[];warnings=[];receipts={};rows_by_year={};note_texts={}
    def source(account,title,receipt,day,quote,year,basis):
        item={'id':f'S{len(sources)+1}','account':account,'title':title,'receipt':receipt,'date':day,'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt,'quote':quote,'year':year,'basis':basis};sources.append(item);return item['id']
    for y in (args.year-1,args.year):
        try:rows=client.financials(company['corp_code'],y,args.basis)
        except (DartError,ValueError):rows=[];warnings.append(f'{y} 재무자료 수집 실패')
        rows_by_year[y]=rows
        rs={r.get('rcept_no') for r in rows if r.get('rcept_no')}
        if len(rs)==1:receipts[y]=next(iter(rs))
        for a in ACCOUNTS:
            v,row=expense(rows,a);values[a][y]={'amount':v,'source':'','method':''}
            if row:
                quote=f"{y}년 {args.basis} / {row['sj_div']} / {row['account_nm']} / 당기 {row['thstrm_amount']} 원"
                sid=source(a,f'사업보고서 ({y}.12) 재무제표',row['rcept_no'],row['rcept_no'][:8],quote,y,args.basis)
                values[a][y].update(source=sid,method=row['sj_div']+'/'+row['account_nm'])
    # Annual discovery also supports cases where financial JSON has no account/report.
    try:filings=client.filings(company['corp_code'],f'{args.year-1}0101',date.today().strftime('%Y%m%d'))
    except (DartError,ValueError):filings=[];warnings.append('관련 공시 목록 조회 실패')
    for y in (args.year-1,args.year):
        annual=[f for f in filings if '사업보고서' in f['report_nm'] and f'({y}.12)' in f['report_nm']]
        if y not in receipts and annual:receipts[y]=max(annual,key=lambda x:x['rcept_no'])['rcept_no']
        receipt=receipts.get(y)
        if not receipt: warnings.append(f'{y} 사업보고서 미확인');continue
        try:text=client.document(receipt,args.basis)
        except (DartError,ValueError):warnings.append(f'{y} 해당 범위 주석 원문 없음/조회 실패');continue
        note_texts[y]=text
        for a in ACCOUNTS:
            for quote in excerpts(text,a,args.max_excerpts):source(a,f'사업보고서 ({y}.12) {args.basis} 감사보고서',receipt,receipt[:8],quote,y,args.basis)
    triggered=[a for a in ACCOUNTS if any(values[a][y]['amount'] is None for y in (args.year-1,args.year)) or change(values[a][args.year]['amount'],values[a][args.year-1]['amount'],args.pct,args.amount)[2]]
    tokens={'이자비용':r'차입|사채|금전대차|증권신고서|증권발행실적보고서|상환|차환'}
    related=[f for f in filings if f['rcept_dt'][:4]==str(args.year) and any(re.search(tokens[a],f['report_nm']) for a in triggered)]
    related.sort(key=lambda f:f['rcept_no'],reverse=True)
    if len(related)>args.max_filings:warnings.append(f'관련공시 {len(related)}건 중 최근 {args.max_filings}건만 탐색')
    for f in related[:args.max_filings]:
        try:text=client.document(f['rcept_no'],args.basis,annual=False)
        except (DartError,ValueError):warnings.append(f"공시 원문 조회 실패 {f['rcept_no']}");continue
        for a in triggered:
            for quote in excerpts(text,a,2):source(a,f['report_nm'],f['rcept_no'],f['rcept_dt'],quote,args.year,'event')
    return company['corp_name'],values,sources,warnings,rows_by_year,note_texts,receipts

def analyze(args,values,sources,answer,ai_status):
    index={s['id']:s for s in sources};reports=[]
    mapped={a.account:a for a in answer.accounts} if answer else {}
    for account in ACCOUNTS:
        ai=mapped.get(account);facts=[];issues=[];expected=None;difference=None;comparison='자료 부족';rate_note=''
        if ai:
            valid_facts=[f for f in ai.facts if citation(f,index) and index[f.source_id]['account']==account]
            facts=[f'{f.statement} [{f.source_id}]' for f in valid_facts]
            if len(valid_facts)!=len(ai.facts):issues.append('일치하지 않는 사실 인용 제외')
            # Do not replace financial JSON. Only fill absent annual amounts with checked note citations.
            for y in (args.year-1,args.year):
                candidates=[]
                for m in ai.amounts:
                    src=index.get(m.source_id,{})
                    if m.year!=y or src.get('year')!=y or src.get('basis')!=args.basis or src.get('account')!=account or not citation(m,index):continue
                    v=number(m.value)
                    if v is None or v<0 or m.unit not in m.quote:continue
                    if not re.search(r'(?<![\d.])'+re.escape(m.value.replace(',',''))+r'(?![\d.])',m.quote.replace(',','')):continue
                    if account not in re.sub(r'\s+','',m.quote):continue
                    if '당기' not in m.quote and str(y) not in m.quote:continue
                    candidates.append((v*{'원':1,'천원':1000,'백만원':1000000,'억원':100000000}[m.unit],m.source_id))
                if values[account][y]['amount'] is None and len({v for v,_ in candidates})==1:
                    values[account][y].update(amount=candidates[0][0],source=candidates[0][1],method='AI 주석 추출(인용·숫자 검증)')
            if ai.expectation:
                try:
                    if ai.expectation.kind!='interest':raise ValueError('이자비용 기대값 계정 불일치')
                    expected=calculate(ai.expectation,index,args.year)
                    comparison='연간 전체 비용' if ai.expectation.coverage=='full_annual' else '개별 건 증분 기대값; 전체 비용과 직접 대사 불가'
                except (ValueError,ArithmeticError):issues.append('기대값 입력 근거 부족/오류: 계산 유보')
            if ai.rates:
                try:
                    old=Decimal(input_value(ai.rates.old_percent,index));new=Decimal(input_value(ai.rates.new_percent,index))
                    if not (0<=old<=100 and 0<=new<=100):raise ValueError()
                    rate_note=f'기존 {old}% / 신규 {new}% / 차이 {new-old}%p. '+('금리상승 검토기준 충족. ' if new-old>=args.rate_pp else '')+ai.rates.conditions+'; 통화·만기·담보·신용조건 비교 필요'
                except (ValueError,ArithmeticError):issues.append('금리 비교 인용/수치 미확인')
        current=values[account][args.year]['amount'];previous=values[account][args.year-1]['amount']
        delta,rate,flag,status=change(current,previous,args.pct,args.amount)
        methods={values[account][y]['method'] for y in (args.year-1,args.year)}
        if len(methods)>1 and current is not None and previous is not None:
            delta=rate=None;flag=False;status='정의/수집경로 차이: 비교 유보'
        if expected is not None and ai.expectation.coverage=='full_annual' and current is not None:difference=current-expected
        if ai and not valid_facts:
            causes='자료 부족: 검증된 공시 사실 없음';risks='공시만으로 위험 판단 불가';procedures=PROCEDURES[account]
        else:
            causes='\n'.join(ai.causes) if ai else 'AI 미사용: 수치 분석 및 원문 발췌만 제공'
            risks='\n'.join(ai.risks) if ai else ('미설명 변동: 누락·기간귀속·측정 오류 가능성 검토(확정 아님)' if flag else '자동 위험 판정 없음')
            procedures='\n'.join(ai.procedures) if ai else PROCEDURES[account]
        reports.append([account,current,previous,delta,rate,'\n'.join(facts) or 'AI 확인 사실 없음; 근거 시트 참조',causes,expected,difference,comparison,rate_note or '자료 없음',risks,procedures,'\n'.join(ai.unknowns) if ai else '공시 내용의 원인 설명력은 감사인 확인 필요',status+' / '+ai_status+' / '+'; '.join(issues),'; '.join(values[account][y]['source'] for y in (args.year-1,args.year)),f'{args.basis}; KRW; {args.year}/{args.year-1}; '+', '.join(sorted(methods))])
    return reports

def safe(v):
    if isinstance(v,Decimal):return float(v)
    if v is None:return '자료 없음'
    if isinstance(v,str):return "'"+v if v.startswith(('=','+','-','@')) else v[:32000]
    return v
def export(path,company,year,reports,sources,warnings,overall_report=None):
    w=Workbook();w.remove(w.active)
    sheets=[('분석적절차 결과',['분석 계정','당기 금액(원)','전기 금액(원)','증감액(원)','증감률','공시 확인 사실','변동 원인 후보(AI)','예상 비용(원)','실제-예상(원)','기대값 적용 범위','금리 비교','잠재적 왜곡표시위험 후보','추가 감사절차','추가 확인사항','분석 상태','금액 출처 ID','범위·단위·연도'],reports),('관련 공시 근거',['관련 계정','공시명','공시일','접수번호','DART URL','인용문','출처 ID','범위','보고연도'],[[s[k] for k in ['account','title','date','receipt','url','quote','id','basis','year']] for s in sources])]
    if overall_report is not None:
        sheets.append(('이자비용 Overall Test',['항목','공시 기반 분석값'],formatted_rows(overall_report)))
        bond_rows=[]
        for bond,amount,method in overall_report['bond_checks']:
            bond_rows.append([bond.name,bond.currency,bond.previous,bond.current,
                bond.rate,bond.issued.isoformat() if bond.issued else '확인되지 않음',
                bond.maturity.isoformat() if bond.maturity else '확인되지 않음',
                amount,method,bond.receipt,
                'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+bond.receipt if bond.receipt else ''])
        sheets.append(('사채별 금리·기대이자',['사채명','통화','전기말 원금대용(원)','당기말 원금대용(원)','표면금리(%)',
            '발행일','만기일','당기 쿠폰이자 근사(원)','계산 가정·한계','사업보고서 접수번호','공시 URL'],bond_rows))
    for name,headers,rows in sheets:
        s=w.create_sheet(name);s.append([safe(f'{company} {year} | 위험평가 보조도구 · 오류/위험 확정 아님')]);s.append([safe('; '.join(warnings) or '수집 경고 없음')]);s.append(headers)
        for row in rows:s.append([safe(v) for v in row])
        s.freeze_panes='B4';s.auto_filter.ref=f'A3:{s.cell(s.max_row,len(headers)).coordinate}'
        for row in s:
            for c in row:c.font=Font(name='맑은 고딕',size=10);c.alignment=Alignment(vertical='top',wrap_text=True)
        for c in s[3]:c.fill=PatternFill('solid',fgColor='17324D');c.font=Font(name='맑은 고딕',bold=True,color='FFFFFF')
        for col in s.columns:s.column_dimensions[col[0].column_letter].width=28
        for n in range(4,s.max_row+1):s.row_dimensions[n].height=110
        if name=='분석적절차 결과':
            for n in range(4,s.max_row+1):
                for col in (2,3,4,8,9):s.cell(n,col).number_format='#,##0;(#,##0)'
                s.cell(n,5).number_format='0.0%'
        elif name=='관련 공시 근거':s.column_dimensions['F'].width=100
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);w.save(path)

def demo(year,basis):
    values={'이자비용':{
        year-1:{'amount':Decimal('10000000000'),'source':'D1','method':'DEMO'},
        year:{'amount':Decimal('13000000000'),'source':'D1','method':'DEMO'}}}
    sources=[{'id':'D1','account':'이자비용','title':'가상 테스트 공시(실제 공시 아님)',
        'receipt':'DEMO','date':str(year)+'0101','url':'',
        'quote':'합성 데이터: 전년 대비 30% 증가. 실제 기업 분석이 아님.',
        'year':year,'basis':basis}]
    return '가상기업',values,sources,['DEMO: 모든 수치·공시는 가상 데이터; 외부 API 미호출']

def main():
    load_dotenv(Path(__file__).with_name('.env'))
    p=argparse.ArgumentParser(description='이자비용 Overall Test 및 분석적절차 MVP')
    p.add_argument('--company',required=True);p.add_argument('--year',type=int,required=True);p.add_argument('--output',default='분석결과.xlsx');p.add_argument('--basis',choices=['CFS','OFS'],default='CFS');p.add_argument('--pct',type=Decimal,default=Decimal('.20'));p.add_argument('--amount',type=Decimal,default=Decimal('1000000000'));p.add_argument('--rate-pp',type=Decimal,default=Decimal('1'));p.add_argument('--overall-tolerance',type=Decimal,default=Decimal('0.05'));p.add_argument('--cache',default='.cache');p.add_argument('--max-filings',type=int,default=8);p.add_argument('--max-excerpts',type=int,default=4);p.add_argument('--no-ai',action='store_true');p.add_argument('--demo',action='store_true');args=p.parse_args()
    if not 2016<=args.year<date.today().year or any(not x.is_finite() or x<0 for x in (args.pct,args.amount,args.rate_pp,args.overall_tolerance)) or args.max_filings<1 or args.max_excerpts<1:p.error('완료된 사업연도(2016년 이후), 유한한 비음수 기준 및 양수 탐색 건수를 지정하세요.')
    try:company,values,sources,warnings,rows_by_year,note_texts,receipts=(demo(args.year,args.basis)+({},{},{}) if args.demo else collect(args))
    except (DartError,ValueError):
        company=args.company;values={a:{y:{'amount':None,'source':'','method':''} for y in (args.year-1,args.year)} for a in ACCOUNTS};sources=[];warnings=['기업 식별/수집 실패: 회사명·DART 키·네트워크 확인. 수치 미입력.'];rows_by_year={};note_texts={};receipts={}
    answer,status=(None,'AI 미사용') if args.no_ai or args.demo or not sources else ask({'company':company,'year':args.year,'basis':args.basis,'amounts':values,'sources':sources},args.cache)
    reports=analyze(args,values,sources,answer,status)
    interest_test=overall(args.year,rows_by_year.get(args.year-1,[]),rows_by_year.get(args.year,[]),
        note_texts.get(args.year,''),note_texts.get(args.year-1,''),
        actual_current=values['이자비용'][args.year]['amount'],
        actual_prior=values['이자비용'][args.year-1]['amount'],
        tolerance_abs=args.amount,tolerance_pct=args.overall_tolerance,
        receipt=receipts.get(args.year,'')) if not args.demo else None
    export(args.output,company,args.year,reports,sources,warnings,interest_test)
    print(f'저장: {Path(args.output).resolve()} / {status}')
    for warning in warnings:print(warning)
if __name__=='__main__':main()
