"""Interest expense overall-test CLI. Calculate variance, draft audit procedures, and collect independent disclosures."""
import argparse
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from core import overall_test, interpret_disclosure_coverage
from audit_ai import generate_review, fallback_review, direction
from opendart import Dart, DartError, account_values, note_interest, debt_balance, receipt_from_rows


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
    # Independent company context: collect regardless of the review threshold.
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
    info['year']=year
    info['disclosure_total']=total
    info['disclosure_status']='검색 완료' if not any(c.startswith('관련 공시 검색 실패') for c in info.get('cautions',[])) else '검색 실패'
    if info['disclosure_status']=='검색 실패':
        explanation='관련 공시 검색 실패: 공시 없음으로 해석할 수 없습니다.'
    if total>max_events:
        explanation+=f' 검색 결과 {total}건 중 최신 {max_events}건만 발췌.'
    return result,info,events,explanation


def _xl(value):
    if value is None:return '자료 없음'
    if isinstance(value,Decimal):return float(value)
    if isinstance(value,bool):return '예' if value else '아니오'
    if isinstance(value,str) and value[:1] in ('=','+','-','@'):return "'"+value
    return str(value) if not isinstance(value,(int,float)) else value


def write_excel(destination, result, info, events, explanation, review=None):
    review=review or fallback_review(result,events)
    w=Workbook()
    s=w.active;s.title='이자비용 차이 분석'
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
        ('실제−기대 절대차이 기준(원, 초과)',result.tolerable_difference),
        ('전기대비 실제 이자비용 변동(원)',result.yoy_change),
        ('전기대비 절대변동률',result.yoy_change_ratio),
        ('전기대비 절대변동 기준(원, 이상)',result.yoy_difference_threshold),
        ('추가검토 기준 충족 (OR)',result.needs_additional_review),
        ('판정',result.conclusion),('계산식',result.method),
        ('전기 이자 출처',info['prior_method']),('당기 이자 출처',info['current_method']),
        ('전기 사업보고서',f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={info['prior_receipt']}" if info['prior_receipt'] else '미확인'),
        ('당기 사업보고서',f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={info['current_receipt']}" if info['current_receipt'] else '미확인'),
        ('재무제표 및 계산 제약','\n'.join(result.warnings+tuple(info['cautions']))),
        ('차이 방향',direction(result)),
        ('해석','기대값 대비 높음·낮음은 과소·과대계상 확정이 아닙니다. 10억원/10%는 내부 검토기준입니다.'),
    ]
    for key,value in headers:s.append([key,_xl(value)])
    p=w.create_sheet('추가 감사절차')
    p.append(['추가 감사절차 · 감사인 검토 초안'])
    p.append(['차이 방향',review['direction'],'생성 상태',review['status']])
    p.append(['구체적 절차는 제안이며 기준서의 문구 자체가 아닙니다. 과소·과대계상을 확정하지 않습니다.'])
    p.append(['검토사항','제안 감사절차','필요 증빙','관련 기준서·문단','기준서 근거 요약','근거 확인 경로','기준서 게시목록 링크'])
    for row in review['procedures']:
        p.append([_xl(row[k]) for k in ('risk','procedure','evidence','standard','basis','source','source_url')])
    t=w.create_sheet('차입 관련 공시')
    t.append(['차입 관련 이슈·공시 · 차이 분석과 별도 참고정보'])
    t.append(['검색 범위',str(info.get('year',''))+'년 / 해당 법인 제목 키워드 / 최신 공시 발췌', '검색 상태',info.get('disclosure_status','검색 완료')])
    t.append(['검색 결과',explanation,'요약 상태',review['status']])
    t.append(['공시명','공시일','접수번호','DART 원본 링크','원문 발췌','AI 공시 요약','감사 참고사항'])
    for ev in review['events']:
        t.append([_xl(ev[k]) for k in ('title','date','receipt','url','quote','summary','consideration')])
    from openpyxl.utils import get_column_letter
    import math
    for ws,widths in ((s,[48,100]),(p,[28,66,40,38,66,38,55]),(t,[40,14,20,54,75,65,55])):
        ws.sheet_view.showGridLines=False
        ws.freeze_panes='B2' if ws is s else 'B5'
        ws.sheet_properties.pageSetUpPr.fitToPage=True
        ws.page_setup.orientation='landscape'
        ws.page_setup.paperSize=ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth=1
        ws.page_setup.fitToHeight=0
        ws.print_title_rows='1:1' if ws is s else '1:4'
        for i,width in enumerate(widths,1):
            ws.column_dimensions[get_column_letter(i)].width=width
        if ws is not s:
            ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=7)
            if ws is p:ws.merge_cells(start_row=3,start_column=1,end_row=3,end_column=7)
            ws.auto_filter.ref=f'A4:G{max(4,ws.max_row)}'
        for row in ws.iter_rows():
            max_lines=1
            for cell in row:
                cell.font=Font(name='맑은 고딕',size=10)
                cell.alignment=Alignment(vertical='top',wrap_text=True)
                if cell.row==1 or (ws is not s and cell.row==4):
                    cell.fill=PatternFill('solid',fgColor='16324F')
                    cell.font=Font(name='맑은 고딕',size=11,bold=True,color='FFFFFF')
                if isinstance(cell.value,str):
                    # Approximate display width for Korean text; cap at Excel's maximum row height.
                    max_lines=max(max_lines,sum(max(1,math.ceil(sum(2 if ord(c)>127 else 1 for c in line)/widths[cell.column-1])) for line in cell.value.split('\n')))
                    if cell.value.startswith('https://'):
                        cell.hyperlink=cell.value
                        cell.font=Font(name='맑은 고딕',size=10,color='0563C1',underline='single')
            ws.row_dimensions[row[0].row].height=min(409,max(24,max_lines*16+8))
        if ws is s:
            for ix in range(2,ws.max_row+1):
                label=ws.cell(ix,1).value
                if label in ('차이율 = |차이|÷|기대|','전기대비 절대변동률'):
                    ws.cell(ix,2).number_format='0.00%'
                elif isinstance(ws.cell(ix,2).value,(int,float)) and label!='사업연도':
                    ws.cell(ix,2).number_format='#,##0.00;(#,##0.00)'
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    w.save(path)
    return path


def main():
    load_dotenv(Path(__file__).with_name('.env'))
    p=argparse.ArgumentParser(description='이자비용 차이 분석 + 기준서 근거 AI 감사절차 + 별도 차입공시 요약')
    p.add_argument('--company',required=True,help='예: LG에너지솔루션, 대한항공, 종목코드')
    p.add_argument('--year',required=True,type=int,help='예: 2025')
    p.add_argument('--basis',choices=['CFS','OFS'],default='CFS')
    p.add_argument('--amount',type=Decimal,default=Decimal('1000000000'),help='실제−기대 절대차이 금액 기준(원, 초과)')
    p.add_argument('--pct',type=Decimal,default=Decimal('0.10'),help='전기 실제 이자비용 대비 절대변동 기준(이상): 기본 10%%')
    p.add_argument('--no-ai',action='store_true',help='AI 없이 기본 절차 예시와 원문 발췌 생성')
    p.add_argument('--model',default=os.getenv('OPENAI_MODEL','gpt-4o-mini'),help='Structured Outputs 지원 OpenAI 모델')
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
    review=generate_review(result,info,events,api_key=None if a.no_ai else os.getenv('OPENAI_API_KEY'),model=a.model)
    out=write_excel(a.output,result,info,events,explanation,review)
    print(f'생성: {out.resolve()}')
    print(f'당기 기대이자비용: {result.expected_interest if result.expected_interest is not None else "자료 부족"}')
    print(f'장부상 실제 이자비용: {result.actual_interest if result.actual_interest is not None else "자료 부족"}')
    print(f'차이: {result.variance if result.variance is not None else "자료 부족"}')
    print(f'관련 공시: {info["disclosure_status"]} / {len(events)}건')
    print(review['status'])
    print(result.conclusion)


if __name__=='__main__':main()
