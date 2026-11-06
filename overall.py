"""Disclosure-based interest overall test; never presents a proxy as independent audit proof."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

MILLION = Decimal("1000000")
D = Decimal
DAY = re.compile(r"20\d\d[.-]\d\d[.-]\d\d")
AMOUNT = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?(?:\s*\(.*\))?$")
RATE = re.compile(r"^\d+(?:\.\d+)?%?$")
IGNORE = ("합 계", "소 계", "차감", "할인발행차금", "총 계")

def num(s):
    if s is None: return None
    s = str(s).strip().replace(",", "")
    if s.startswith("(") and s.endswith(")"): s = "-" + s[1:-1]
    try: return D(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None
    except InvalidOperation: return None

def date_value(s):
    m = DAY.search(str(s))
    if not m: return None
    try: return date.fromisoformat(m.group().replace(".", "-"))
    except ValueError: return None

def rate_value(s):
    s = str(s).strip()
    return num(s.rstrip("%")) if RATE.fullmatch(s) else None

def amount_cell(s):
    s = str(s).strip()
    m = re.match(r"^([\d,]+)(?:\s*\(.*\))?$", s)
    return num(m.group(1)) if m else None

def cells(line):
    return [x.strip() for x in line.strip().strip("|").split("|")]

def note(text, title):
    # Use the numbered note heading, not a matching table of contents entry.
    pattern = re.compile(r"(?m)^\s*\d{1,2}\.\s*" + re.escape(title) + r"\s*(?:\n|$)")
    matches = list(pattern.finditer(text or ""))
    if not matches: return ""
    m = matches[-1]
    stop = re.search(r"(?m)^\s*\d{1,2}\.\s*[^|\n]{2,80}(?:\n|$)", text[m.end():])
    return text[m.end():m.end()+stop.start()] if stop else text[m.end():]

def interest_note(text):
    """Return (current, previous, current_capitalized, previous_capitalized) in KRW.
    The row must be in the financial-income/cost note and its unit must be disclosed.
    """
    section = note(text, "금융수익 및 금융비용")
    if not section: return (None, None, None, None)
    unit_m = re.search(r"단위\s*[:：]\s*(백만원|천원|억원|원)", section)
    if not unit_m: return (None, None, None, None)
    multiplier = {"원":D(1),"천원":D(1000),"백만원":MILLION,"억원":D(100000000)}[unit_m.group(1)]
    interest = cap = None
    for line in section.splitlines():
        cs = cells(line)
        if len(cs) < 3: continue
        label = cs[0].replace(" ","")
        a, b = num(cs[1]), num(cs[2])
        if a is None or b is None: continue
        if re.fullmatch(r"이자비용(?:\(\*\d+\))?",label):
            interest = (a*multiplier,b*multiplier)
        if "적격자산원가에포함되어있는금액" in label:
            cap = (-a*multiplier, -b*multiplier) # disclosed as a negative deduction
    return (*(interest or (None,None)),*(cap or (None,None)))

def financial_row(rows, year_col="thstrm_amount", name=None):
    if not name: return None
    items=[r for r in rows if r.get("sj_div") == "BS" and
           re.sub(r"\s+","",r.get("account_nm","")) == name and
           r.get("account_detail","-") in ("-","") and r.get("currency") == "KRW"]
    vals={num(r.get(year_col)) for r in items}
    return next(iter(vals)) if len(vals)==1 else None

def debt_amount(rows, column):
    # Combined borrowing captions include all bonds and leases: do not add
    # the detailed lines again.
    common=["유동성차입금","비유동성차입금"]
    if all(financial_row(rows,column,k) is not None for k in common):
        return sum((financial_row(rows,column,k) for k in common),D(0))*D(1)
    names=["단기차입금","유동성장기부채","유동성리스부채",
           "장기차입금","사채","리스부채","자산유동화차입금"]
    vals=[financial_row(rows,column,k) for k in names]
    if all(v is None for v in vals): return None
    # At least the core financing captions must be present.
    if not all(financial_row(rows,column,k) is not None for k in ("단기차입금","장기차입금","사채")): return None
    return sum((v for v in vals if v is not None), D(0))

@dataclass
class Bond:
    name: str
    maturity: date | None
    rate: Decimal | None
    previous: Decimal
    current: Decimal
    issued: date | None
    currency: str
    receipt: str

    @property
    def key(self): return (self.name,self.maturity)

def extract_bonds(text, receipt):
    """Read fixed-coupon bond tables from the numbered borrowing note.
    All principal amounts in notes are in KRW millions, including USD bonds'
    translated balance; this is NOT an FX-adjusted cash interest model.
    """
    section = note(text,"차입금") or note(text,"차입금 및 사채")
    if not section: return []
    if not re.search(r"단위\s*[:：]\s*(?:USD천,\s*)?백만원",section): return []
    out=[]
    in_bonds=False; phase="current"
    for line in section.splitlines():
        compact=re.sub(r"\s+","",line)
        if re.search(r"(?:^|\s)3\)\s*사채",line) or re.search(r"\(3\).*사채의내역",compact):
            in_bonds=True
        if in_bonds and (re.match(r"\s*4\)",line) or re.match(r"\s*\(4\)",line)):
            break
        if not in_bonds: continue
        if "구분" in compact and "전기말" in compact and "당기말" not in compact: phase="previous"
        elif "구분" in compact and "당기말" in compact and "전기말" not in compact: phase="current"
        cs=cells(line)
        if len(cs)<5: continue
        name=cs[0]
        if not ("사채" in name or "회사채" in name): continue
        if any(x in name for x in IGNORE): continue
        dt=[(i,date_value(v)) for i,v in enumerate(cs[1:],1) if date_value(v)]
        if not dt: continue
        maturity=dt[-1][1]
        if "외화사채" in name: currency="USD"
        elif "EUR" in name: currency="EUR"
        else: currency="KRW"
        if len(dt)>=2:  # issue date and maturity, e.g. Korean Air
            issued=dt[0][1]
            k=dt[-1][0]+1
            rate=rate_value(cs[k]) if k<len(cs) else None
            a=amount_cell(cs[k+1]) if k+1<len(cs) else None
            b=amount_cell(cs[k+2]) if k+2<len(cs) else None
            cur=a or D(0); prev=b or D(0)
        else: # maturity only, e.g. LG Energy Solution
            issued=None
            rate_idx=next((j for j in range(1,dt[0][0]) if rate_value(cs[j]) is not None),None)
            rate=rate_value(cs[rate_idx]) if rate_idx is not None else None
            a=amount_cell(cs[dt[0][0]+1]) if dt[0][0]+1<len(cs) else None
            if a is None: continue
            cur=a if phase=="current" else D(0)
            prev=a if phase=="previous" else D(0)
        if not cur and not prev: continue
        out.append(Bond(name,maturity,rate,prev*MILLION,cur*MILLION,issued,currency,receipt))
    # The same instrument may be listed in consecutive current/previous tables.
    merged={}
    for item in out:
        key=item.key
        if key not in merged: merged[key]=item;continue
        p=merged[key]
        merged[key]=Bond(p.name,p.maturity,p.rate or item.rate,
                          max(p.previous,item.previous),max(p.current,item.current),
                          p.issued or item.issued,p.currency,p.receipt)
    return list(merged.values())

def weighted(bonds, field, currency="KRW"):
    relevant=[b for b in bonds if b.currency==currency and b.rate is not None and getattr(b,field)>0]
    total=sum((getattr(b,field) for b in relevant),D(0))
    return (sum((getattr(b,field)*b.rate for b in relevant),D(0))/total if total else None,total,len(relevant))

def bond_expectations(bonds, year):
    """Partial coupon proxy ONLY: snapshot balance may not equal daily principal."""
    outputs=[]
    for b in bonds:
        if b.rate is None or not b.maturity: continue
        if b.previous>0: start=date(year,1,1); principal=b.previous; method="전기말잔액 유지 가정"
        elif b.current>0 and b.issued:
            start=b.issued;principal=b.current;method="당기 발행원금 대용 기말잔액"
        else:
            outputs.append((b,None,"신규발행일 미확인"))
            continue
        end=min(b.maturity,date(year,12,31))
        days=max((end-max(start,date(year,1,1))).days+1,0)
        amount=principal*b.rate/D(100)*D(days)/D((date(year+1,1,1)-date(year,1,1)).days)
        outputs.append((b,amount,method))
    return outputs

def overall(year, rows_prior, rows_current, notes_current, notes_prior,
            actual_current=None, actual_prior=None, tolerance_abs=D("1000000000"),
            tolerance_pct=D("0.05"), receipt=""):
    """Independent historical-rate/average-balance screening, NOT contract-level assurance."""
    previous_year_debt=debt_amount(rows_prior,"frmtrm_amount")
    prior_debt=debt_amount(rows_prior,"thstrm_amount")
    current_debt=debt_amount(rows_current,"thstrm_amount")
    val, old_comparative, cap_current, cap_prior = interest_note(notes_current)
    old_same_year,_,cap_old,_ = interest_note(notes_prior)
    actual_current=actual_current if actual_current is not None else val
    actual_prior=actual_prior if actual_prior is not None else (old_comparative if old_comparative is not None else old_same_year)
    if cap_prior is None: cap_prior=cap_old
    issues=[]
    if val is not None and actual_current is not None and val!=actual_current:
        issues.append("재무자료와 주석 이자비용 불일치: 비교·재작성 여부 확인")
    if old_comparative is not None and old_same_year is not None and old_comparative != old_same_year:
        issues.append("전기 사업보고서와 당기 비교표시 이자비용 차이: 재작성 여부 대사")
    avg_prior=(previous_year_debt+prior_debt)/2 if previous_year_debt is not None and prior_debt is not None else None
    avg_current=(prior_debt+current_debt)/2 if prior_debt is not None and current_debt is not None else None
    prior_gross=actual_prior+cap_prior if actual_prior is not None and cap_prior is not None else actual_prior
    current_gross=actual_current+cap_current if actual_current is not None and cap_current is not None else actual_current
    historical_rate=prior_gross/avg_prior if prior_gross is not None and avg_prior and avg_prior>0 else None
    current_rate=current_gross/avg_current if current_gross is not None and avg_current and avg_current>0 else None
    expected_gross=historical_rate*avg_current if historical_rate is not None and avg_current is not None else None
    expected_net=(expected_gross-cap_current if cap_current is not None else expected_gross) if expected_gross is not None else None
    diff=actual_current-expected_net if actual_current is not None and expected_net is not None else None
    threshold=max(D(tolerance_abs),abs(actual_current)*D(tolerance_pct)) if actual_current is not None else None
    flagged=abs(diff)>=threshold if diff is not None and threshold is not None else None
    if cap_current is None or cap_prior is None: issues.append("차입원가 자본화 자료 부족: 비용화 이자부담률만 사용한 근사 검토(자본화 조정 생략); 실제 전체 이자 재계산 아님")
    if historical_rate is None: issues.append("전기 평균부채 또는 자본화 전 이자자료 부족")
    issues.append("잔액은 기초·기말 단순평균: 차입·상환 시점, 환율, 변동금리, 리스 효과 검증 필요")
    if flagged: issues.append("기준 초과 차이: 차입원장·원리금상환표·계약서로 상세 재계산 필요")
    bonds=extract_bonds(notes_current,receipt)
    old_rate,old_total,old_count=weighted(bonds,"previous")
    new_bonds=[b for b in bonds if b.current>0 and b.previous==0]
    new_rate,new_total,new_count=weighted(new_bonds,"current")
    post_rate,post_total,post_count=weighted(bonds,"current")
    bond_checks=bond_expectations(bonds,year)
    partial=sum((x for _,x,_ in bond_checks if x is not None),D(0))
    missing=sum(1 for bond,amount,_ in bond_checks if bond.rate is None or amount is None)
    if not bonds: issues.append("사채별 잔액·금리표 추출 실패: 계약별 기대이자 미계산")
    if missing: issues.append(f"회사채 {missing}건은 계약금리 또는 발행일 확인 불가")
    return {"year":year,"actual":actual_current,"previous":actual_prior,
            "debt_older":previous_year_debt,"debt_previous":prior_debt,"debt_current":current_debt,
            "average_previous":avg_prior,"average_current":avg_current,
            "cap_previous":cap_prior,"cap_current":cap_current,
            "gross_previous":prior_gross,"gross_current":current_gross,
            "implied_rate_previous":historical_rate,"implied_rate_current":current_rate,
            "expected_gross_proxy":expected_gross,"expected_net_proxy":expected_net,
            "difference_proxy":diff,"screening_threshold":threshold,"screening_flag":flagged,
            "existing_bond_rate":old_rate,"new_bond_rate":new_rate,"post_bond_rate":post_rate,
            "existing_bond_principal":old_total,"new_bond_principal":new_total,"post_bond_principal":post_total,
            "bond_coverage":{"existing":old_count,"new":new_count,"post":post_count,
                             "coupon_proxy":len(bond_checks),"missing":missing},
            "bonds":bonds,"bond_checks":bond_checks,"issues":issues,
            "method":("전기 자본화 전 이자비용/전기 평균 이자발생부채 × 당기 평균 이자발생부채 − 당기 자본화 금액" if cap_current is not None and cap_prior is not None else "전기 비용화 이자비용/전기 평균 이자발생부채 × 당기 평균 이자발생부채 (자본화 자료 미확인, 비교지표)"),
            "confidence":"공시 기반 근사 Overall Test; 전체 계약별 독립 검증 미완료; 허용차이 초과는 검토 신호이지 왜곡표시 결론이 아님",
            "procedures":[
              "차입금·사채·리스부채 모집단을 총계정원장, 차입금명세, 은행조회서와 대사",
              "계약별 원금·차입/상환일·금리 재설정일·일별잔액으로 독립적 이자 재계산",
              "신규·차환채 금리와 기존 채무의 통화·만기·담보 및 보증조건 비교",
              "사채할인발행차금·유효이자율·외화환산·차입원가 자본화 적정성 검토",
              "연결대상 변동·리스부채 이자 및 내부거래 제거의 전기 대비 비교가능성 평가",
              "독립 기대값과 실제의 차이를 재계산하고 중요성/허용차이 및 추가 실증절차 결정"]}

def formatted_rows(result):
    fields=[("분석연도","year"),("당기 실제 이자비용(원)","actual"),("전기 이자비용(원)","previous"),
      ("전전기말 이자발생부채(원)","debt_older"),("전기말 이자발생부채(원)","debt_previous"),
      ("당기말 이자발생부채(원)","debt_current"),("전기 평균잔액(원)","average_previous"),
      ("당기 평균잔액(원)","average_current"),("전기 자본화 전 이자(원)","gross_previous"),
      ("당기 자본화 전 이자(원)","gross_current"),("전기 이자부담률(비율)","implied_rate_previous"),
      ("당기 이자부담률(비율)","implied_rate_current"),("당기 기대이자: 자본화 전(원)","expected_gross_proxy"),
      ("당기 자본화 금액(원)","cap_current"),("당기 기대이자: 자본화 후(원)","expected_net_proxy"),
      ("실제−기대(원)","difference_proxy"),("자동 검토 기준(원)","screening_threshold"),
      ("검토 기준 초과","screening_flag"),("기존 KRW 사채 가중금리(%)","existing_bond_rate"),
      ("신규 KRW 사채 가중금리(%)","new_bond_rate"),("당기말 KRW 사채 가중금리(%)","post_bond_rate"),
      ("기존 KRW 사채 금액(원)","existing_bond_principal"),("신규 KRW 사채 금액(원)","new_bond_principal"),
      ("기대값 계산 방식","method"),("검증수준","confidence"),
      ("미확인사항","issues"),("추가 감사절차","procedures")]
    return [[name, "\n".join(result[key]) if isinstance(result[key],list) else result[key]] for name,key in fields]
