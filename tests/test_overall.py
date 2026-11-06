"""Regression tests for disclosure-based interest screening (synthetic fixtures)."""
import unittest
from datetime import date
from decimal import Decimal
from overall import num,rate_value,interest_note,extract_bonds,weighted,bond_expectations,overall

D=Decimal
def bs(current,previous,older=None):
    out=[]
    for name,pair in {"유동성차입금":(current[0],previous[0]),
                      "비유동성차입금":(current[1],previous[1])}.items():
        out.append({"sj_div":"BS","account_nm":name,"account_detail":"-",
                    "currency":"KRW","thstrm_amount":str(pair[0]),
                    "frmtrm_amount":str(pair[1])})
    return out

NOTE_2025="""24. 금융수익 및 금융비용
| (단위: 백만원) |
| 구 분 | 당기 | 전기 |
| 이자비용(*2) | 817,154 | 564,246 |
| 적격자산원가에 포함되어 있는 금액 | (155,955) | (96,216) |
25. 특수관계자
"""
NOTE_2024="""24. 금융수익 및 금융비용
| (단위: 백만원) |
| 구 분 | 당기 | 전기 |
| 이자비용(*2) | 564,246 | 315,752 |
| 적격자산원가에 포함되어 있는 금액 | (96,216) | (58,015) |
25. 특수관계자
"""
BONDS="""14. 차입금
(2) 상세내역
3) 사채
| (단위: USD천, 백만원) |
| 구 분 | 당기말 |
| 인수기관 | 이자율(%) | 최장만기일 | 원화가액 | 유동성 | 비유동성 |
| 제4-1회 회사채(무보증/공모) | 한국투자증권 등 | 3.138 | 2027.02.14 | 640,000 | - | 640,000 |
| 제4-2회 회사채(무보증/공모) | 한국투자증권 등 | 3.228 | 2028.02.14 | 590,000 | - | 590,000 |
| 구 분 | 전기말 |
| 인수기관 | 이자율(%) | 최장만기일 | 원화가액 |
| 제1회 회사채(무보증/공모) | 한국투자증권 등 | 2.214 | 2026.03.13 | 200,000 | - | 200,000 |
4) 리스부채
15. 충당부채
"""

class OverallTests(unittest.TestCase):
    def test_parsed_interest_and_capitalization(self):
        self.assertEqual(interest_note(NOTE_2025),
                         (D("817154000000"),D("564246000000"),D("155955000000"),D("96216000000")))
    def test_weighted_bond_rates(self):
        bs=extract_bonds(BONDS,"20260312000217")
        self.assertEqual(len(bs),3)
        rate,principal,n=weighted(bs,"current")
        self.assertEqual(principal,D("1230000000000"))
        self.assertEqual(n,2)
        self.assertTrue(D("3.18") < rate < D("3.19"))
    def test_missing_issue_dates_not_fabricated(self):
        bs=extract_bonds(BONDS,"20260312000217")
        checks=bond_expectations(bs,2025)
        self.assertTrue(any(amount is None and "발행일 미확인" in note for _,amount,note in checks))
    def test_full_disclosure_proxy(self):
        # BS values are already KRW, aligned to current note figures.
        prior=bs((D("2490240000000"),D("12900311000000")),
                 (D("3211456000000"),D("7720832000000")))
        current=bs((D("6686206000000"),D("15825890000000")),
                   (D("2490240000000"),D("12900311000000")))
        out=overall(2025,prior,current,NOTE_2025+"\n"+BONDS,NOTE_2024,
                    D("817154000000"),D("564246000000"),tolerance_pct=D("0.01"))
        self.assertIsNotNone(out["expected_net_proxy"])
        self.assertIsNotNone(out["difference_proxy"])
        self.assertEqual(out["cap_current"],D("155955000000"))
        self.assertEqual(out["debt_current"],D("22512096000000"))
        self.assertEqual(out["screening_flag"],True)
        self.assertIn("검토",out["confidence"])
    def test_no_source_no_invented_expectation(self):
        out=overall(2025,[],[],"","",None,None)
        self.assertIsNone(out["expected_net_proxy"])
        self.assertIsNone(out["screening_flag"])
    def test_cost_only_when_capitalization_missing(self):
        note="35. 금융수익 및 금융비용\n| (단위: 백만원) |\n| 구 분 | 당기 | 전기 |\n| 이자비용 | 800,000 | 500,000 |\n36. 다른 주석\n"
        prev=bs((D("4000000000000"),D("5000000000000")),(D("3000000000000"),D("4000000000000")))
        cur=bs((D("5000000000000"),D("6000000000000")),(D("4000000000000"),D("5000000000000")))
        r=overall(2025,prev,cur,note,note,D("800000000000"),D("500000000000"))
        self.assertIsNotNone(r["expected_net_proxy"])
        self.assertTrue(any("자본화" in x for x in r["issues"]))

if __name__=="__main__":
    unittest.main()
