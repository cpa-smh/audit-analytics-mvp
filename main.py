"""Interest expense overall-test CLI. Search event disclosures ONLY after the variance gate."""
import argparse
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from core import overall_test, interpret_disclosure_coverage
from opendart import Dart, DartError, account_values, note_interest, debt_balance, receipt_from_rows


PROCEDURES = (
    '차입금명세와 재무상태표·총계정원장을 대사하고 차입금 모집단의 완전성을 확인',
    '계약별 원금, 실행·상환일, 이자율 변경일을 확인하여 일별 이자비용 독립 재계산',
    '사채할인발행차금의 유효이자율, 미지급이자 기간귀속, 차입원가 자본화를 검토',
    '외화차입 환율, 변동금리, 리스부채 및 연결범위 변경 효과를 분리',
    '공시상 차입·상환 사실을 은행조회서 및 차입약정서와 대사하고 미설명 차이에 대해 추가 실증절차 수행',
)


def collect_historical(dart, company, year, basis):
    """No list.json or event report search here: financials+annual notes only."""
    code, name=dart.company(company)
    older=dart.financials(code,year-1,basis)
    current=dart.financials(code,year,basis)
    old_receipt=receipt_from_rows(older)
    current_receipt=receipt_from_rows(current)
    notes=[]
    for label,receipt in ((str(year-1),old_receipt),(str(year),current_receipt)):
        try:
            notes.append(dart.audit_note(receipt,basis) if receipt else '')
        except (DartError,ValueError):
            notes.append('')
    old_note, new_note=notes
    old_note_current,_,old_cap,_=note_interest(old_note)
    new_note_current,new_note_previous,new_cap,new_cap_previous=note_interest(new_note)
    val_old,method_old=account_values(older,'thstrm_amount')
    val_new,method_new=account_values(current,'thstrm_amount')
    cautions=[]
    if val_old is None and old_note_current is not None:
        val_old=old_note_current;method_old='전기 감사보고서 금융비용 주석'
    if val_new is None and new_note_current is not None:
        val_new=new_note_current;method_new='당기 감사보고서 금융비용 주석'
    if val_old is None and new_note_previous is not None:
        val_old=new_note_previous;method_old='당기 감사보고서 전기 비교 주석'
    if val_old is not None and new_note_previous is not None and val_old!=new_note_previous:
        cautions.append('전기 이자비용의 당기 비교표시 금액 불일치: 재작성·범위 차이 확인 필요')
    if val_new is not None and new_note_current is not None and val_new!=new_note_current:
        cautions.append('당기 이자비용의 재무제표 계정과 주석 금액 불일치: 자료 재확인 필요')
    if val_old is not None and old_note_current is not None and val_old!=old_note_current:
        cautions.append('전기 이자비용의 재무제표 계정과 전기 주석 금액 불일치')
    if old_cap is None and new_cap_previous is not None:
        old_cap=new_cap_previous
    pb,method_pb=debt_balance(older,'frmtrm_amount')
    pe,method_pe=debt_balance(older,'thstrm_amount')
    ce,method_ce=debt_balance(current,'thstrm_amount')
    if pe is not None:
        comparable,other_method=debt_balance(current,'frmtrm_amount')
        if comparable is not None and comparable!=pe:
            cautions.append('전기말 차입금의 당기 비교표시 잔액 불일치: 재작성·범위 변화 여부 확인')
    if len({method_pb,method_pe,method_ce})>1:
        cautions.append('기간별 차입금 추출 방법 불일치: 계정구성 비교가능성 점검')
    if val_old is not None and val_new is not None and method_old!=method_new:
        cautions.append('이자비용 수집 경로 차이: 같은 정의의 이자비용인지 확인')
    info={'code':code,'name':name,'prior_receipt':old_receipt,
          'current_receipt':current_receipt,'prior_method':method_old,
          'current_method':method_new,'debt_methods':(method_pb,method_pe,method_ce),
          'cautions':cautions,'basis':basis}
    return (pb,pe,ce,val_old,val_new,old_cap,new_cap),info


def analyze_company(dart, company, year, basis, min_amount, min_rate, max_events=10):
    values,info=collect_historical(dart,company,year,basis)
    result=overall_test(*values,min_variance_won=min_amount,min_variance_ratio=min_rate)
    events=[];total=0
    # The ONLY call to event disclosure search is inside this guarded branch.
    if result.needs_disclosure_search:
        try:
            filings,total=dart.event_filings(info['code'],year,max_events)
            for filing in filings:
                receipt=filing['rcept_no']
                try: quote=dart.event_excerpt(receipt)
                except (DartError,ValueError): quote='공시 원문 확보 실패: 별도 열람 필요'
                events.append({'title':filing.get('report_nm',''),'date':filing.get('rcept_dt',''),
                               'receipt':receipt,'url':f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}',
                               'quote':quote})
        except (DartError,ValueError) as ex:
            info['cautions'].append('관련 공시 검색 실패: '+str(ex))
    explanation=interpret_disclosure_coverage(result,events)
    if result.needs_disclosure_search and total>max_events:
        explanation+=f' 검색 결과 {total}건 중 최신 {max_events}건만 발췌.'
    return result,info,events,explanation


def _xl(value):
    if value is None:return '자료 없음'
    if isinstance(value,Decimal):return float(value)
    if isinstance(value,bool):return '예' if value else '아니오'
    if isinstance(value,str) and value[:1] in ('=','+','-','@'):return "'"+value
    return str(value) if not isinstance(value,(int,float)) else value


def write_excel(destination, result, info, events, explanation):
    w=Workbook()
    s=w.active;s.title='Overall Test'
    s.append(['이자비용 Overall Test (공시기반 근사 분석)','값'])
    headers=[
        ('회사',info['name']),('사업연도',result and info.get('year','')),('기준',info['basis']),
        ('전전기말 이자발생부채(원)',result.prior_begin_debt),
        ('전기말 이자발생부채(원)',result.prior_end_debt),
        ('당기말 이자발생부채(원)',result.current_end_debt),
        ('전기 평균 차입잔액(원)',result.prior_average_debt),
        ('당기 평균 차입잔액(원)',result.current_average_debt),
        ('전기 이자비용(원)',result.prior_interest),
        ('전기 자본화 차입원가(원)',result.prior_capitalized),
        ('전기 추정 평균 차입이자율(%)',result.implied_prior_rate*100 if result.implied_prior_rate is not None else None),
        ('당기 자본화 차입원가(원)',result.current_capitalized),
        ('당기 기대이자비용(원)',result.expected_interest),
        ('당기 실제 장부상 이자비용(원)',result.actual_interest),
        ('차이 = 실제−기대(원)',result.variance),
        ('차이율 = |차이|÷|기대|',result.variance_ratio),
        ('수용가능 차이 비교기준(원)',result.tolerable_difference),
        ('10% AND 10억원 기준 동시 충족',result.needs_disclosure_search),
        ('판정',result.conclusion),('계산식',result.method),
        ('전기 이자 출처',info['prior_method']),('당기 이자 출처',info['current_method']),
        ('전기 사업보고서',f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={info['prior_receipt']}" if info['prior_receipt'] else '미확인'),
        ('당기 사업보고서',f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={info['current_receipt']}" if info['current_receipt'] else '미확인'),
        ('재무제표 및 계산 제약','\n'.join(result.warnings+tuple(info['cautions']))),
        ('기본 추가 감사절차','\n'.join(PROCEDURES)),
    ]
    for key,value in headers:s.append([key,_xl(value)])
    t=w.create_sheet('관련 공시 검토')
    t.append(['공시 검색 기준 충족','예' if result.needs_disclosure_search else '아니오'])
    t.append(['공시 설명력 결론',explanation])
    t.append(['공시명','공시일','접수번호','DART 원문 URL','확인된 원문 발췌'])
    for ev in events:t.append([_xl(ev[k]) for k in ('title','date','receipt','url','quote')])
    for ws in w:
        ws.freeze_panes='B2' if ws is s else 'B4'
        ws.sheet_view.showGridLines=False
        widths=[44,74] if ws is s else [42,18,20,56,105]
        from openpyxl.utils import get_column_letter
        for i,width in enumerate(widths,1):ws.column_dimensions[get_column_letter(i)].width=width
        for cell in ws[1]:
            cell.fill=PatternFill('solid',fgColor='16324F')
            cell.font=Font(name='맑은 고딕',size=11,bold=True,color='FFFFFF')
        for row in ws.iter_rows():
            for cell in row:
                if cell.row>1:cell.font=Font(name='맑은 고딕',size=10)
                cell.alignment=Alignment(vertical='top',wrap_text=True)
        if ws is s:
            for ix in range(2,ws.max_row+1):
                ws.row_dimensions[ix].height=24 if ix not in (26,27) else 88
                if ws.cell(ix,1).value=='차이율 = |차이|÷|기대|':ws.cell(ix,2).number_format='0.0%'
                elif isinstance(ws.cell(ix,2).value,(int,float)) and ix>3:
                    ws.cell(ix,2).number_format='#,##0.0000;(#,##0.0000)'
        else:
            ws.auto_filter.ref=f'A3:E{max(ws.max_row,3)}'
            for ix in range(4,ws.max_row+1):ws.row_dimensions[ix].height=80
            ws.row_dimensions[2].height=48
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    w.save(path)
    return path


def main():
    load_dotenv(Path(__file__).with_name('.env'))
    p=argparse.ArgumentParser(description='이자비용 Overall Test: 기대값 계산 후 10%·10억 차이 시에만 공시 검색')
    p.add_argument('--company',required=True,help='예: LG에너지솔루션, 대한항공, 종목코드')
    p.add_argument('--year',required=True,type=int,help='예: 2025')
    p.add_argument('--basis',choices=['CFS','OFS'],default='CFS')
    p.add_argument('--amount',type=Decimal,default=Decimal('1000000000'),help='공시검색 차이 금액 기준(원)')
    p.add_argument('--pct',type=Decimal,default=Decimal('0.10'),help='공시검색 차이율 기준: 기본 10%%')
    p.add_argument('--max-filings',type=int,default=10)
    p.add_argument('--cache',default='.cache')
    p.add_argument('--output',default='이자비용_Overall_Test.xlsx')
    a=p.parse_args()
    if a.year<2016 or a.year>=date.today().year or a.amount<0 or a.pct<0 or a.max_filings<1:
        p.error('완료된 사업연도 및 유효한 비음수 기준을 지정하세요.')
    d=Dart(os.getenv('OPENDART_API_KEY',''),a.cache)
    try:
        result,info,events,explanation=analyze_company(d,a.company,a.year,a.basis,a.amount,a.pct,a.max_filings)
    except (DartError,ValueError) as e:
        p.error(str(e))
    info['year']=a.year
    out=write_excel(a.output,result,info,events,explanation)
    print(f'생성: {out.resolve()}')
    print(f'당기 기대이자비용: {result.expected_interest if result.expected_interest is not None else "자료 부족"}')
    print(f'장부상 실제 이자비용: {result.actual_interest if result.actual_interest is not None else "자료 부족"}')
    print(f'차이: {result.variance if result.variance is not None else "자료 부족"}')
    print(f'관련 공시 검색: {"실행" if result.needs_disclosure_search else "미실행"}')
    print(result.conclusion)


if __name__=='__main__':main()
