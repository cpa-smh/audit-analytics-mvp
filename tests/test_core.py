import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from openpyxl import load_workbook

from core import overall_test, relevant_title, interpret_disclosure_coverage
from opendart import debt_balance, note_interest
from main import analyze_company, write_excel


class OverallTestCases(unittest.TestCase):
    def test_expectation_precedes_disclosure_with_exact_threshold(self):
        r=overall_test(200_000_000_000,200_000_000_000,200_000_000_000,
                       10_000_000_000,11_000_000_000)
        self.assertEqual(r.implied_prior_rate,D('0.05'))
        self.assertEqual(r.expected_interest,D('10000000000'))
        self.assertEqual(r.variance,D('1000000000'))
        self.assertEqual(r.variance_ratio,D('0.10'))
        self.assertEqual(r.tolerable_difference,D('1000000000'))
        self.assertTrue(r.needs_disclosure_search)

    def test_search_if_only_amount_threshold_satisfied(self):
        r=overall_test(200_000_000_000,200_000_000_000,200_000_000_000,
                       100_000_000_000,102_000_000_000)
        self.assertTrue(r.needs_disclosure_search)

    def test_search_if_only_percent_threshold_satisfied(self):
        r=overall_test(2_000_000_000,2_000_000_000,2_000_000_000,
                       100_000_000,200_000_000)
        self.assertTrue(r.needs_disclosure_search)

    def test_capitalization_properly_adjusted(self):
        r=overall_test(200,200,300,10,20,2,3,min_variance_won=D('0.1'))
        self.assertEqual(r.implied_prior_rate,D('0.06'))
        self.assertEqual(r.expected_interest,D('12'))
        self.assertEqual(r.variance,D('8'))
        self.assertTrue(r.needs_disclosure_search)

    def test_undefined_or_missing_data_never_invents_rate(self):
        r=overall_test(None,200,300,10,20)
        self.assertIsNone(r.expected_interest)
        self.assertTrue(r.needs_disclosure_search)
        r=overall_test(10,10,10,0,1_500_000_000)
        self.assertIsNone(r.variance_ratio)
        self.assertTrue(r.needs_disclosure_search)

    def test_independent_boundaries_and_decreases(self):
        for delta, search in ((999_999_999,False),(1_000_000_000,False),
                              (1_000_000_001,True),(-1_000_000_000,False),
                              (-1_000_000_001,True)):
            with self.subTest(delta=delta):
                r=overall_test(200_000_000_000,200_000_000_000,200_000_000_000,
                               100_000_000_000,100_000_000_000+delta)
                self.assertEqual(r.needs_disclosure_search,search)
        for actual, search in ((109_999_999,False),(110_000_000,True),
                               (90_000_001,False),(90_000_000,True)):
            with self.subTest(actual=actual):
                r=overall_test(100,100,100,100_000_000,actual)
                self.assertEqual(r.needs_disclosure_search,search)

    def test_yoy_uses_prior_actual_not_expected_or_capitalized(self):
        r=overall_test(100,100,120,100_000_000,110_000_000)
        self.assertEqual(r.variance,0)
        self.assertEqual(r.yoy_change_ratio,D('0.10'))
        self.assertTrue(r.needs_disclosure_search)
        r=overall_test(100,100,200,100_000_000,105_000_000)
        self.assertGreater(r.variance_ratio,D('0.10'))
        self.assertFalse(r.needs_disclosure_search)
        r=overall_test(100,100,100,100_000_000,110_000_000,100_000_000,100_000_000)
        self.assertEqual(r.yoy_difference_threshold,D('10000000'))
        self.assertTrue(r.needs_disclosure_search)

    def test_zero_and_missing_inputs(self):
        for prior, actual, search in ((0,0,True),(0,1,True),
                                      (None,1,False),(1,None,False)):
            with self.subTest(prior=prior,actual=actual):
                r=overall_test(100,100,100,prior,actual)
                self.assertEqual(r.needs_disclosure_search,search)
        r=overall_test(100,100,100,100_000_000,110_000_000,
                       min_variance_ratio=D('0.11'))
        self.assertFalse(r.needs_disclosure_search)

    def test_interest_only_keywords(self):
        self.assertTrue(relevant_title('증권발행실적보고서'))
        self.assertTrue(relevant_title('단기차입금 증가결정'))
        self.assertFalse(relevant_title('신규시설투자'))
        self.assertFalse(relevant_title('사업보고서 (2025.12)'))

    def test_cash_and_bond_debt_categories(self):
        def row(n,v,old):return {'sj_div':'BS','account_nm':n,'currency':'KRW','account_detail':'-','thstrm_amount':str(v),'frmtrm_amount':str(old)}
        rows=[row('유동성차입금',4,3),row('비유동성차입금',7,6)]
        self.assertEqual(debt_balance(rows,'thstrm_amount')[0],11)
        rows=[row('단기차입금',4,3),row('유동성장기부채',2,2),row('유동성리스부채',1,1),row('장기차입금',7,6),row('사채',5,4),row('리스부채',3,2)]
        self.assertEqual(debt_balance(rows,'thstrm_amount')[0],22)

    def test_note_interest_only(self):
        note='''24. 금융수익 및 금융비용
| (단위: 백만원) |
| 구 분 | 당기 | 전기 |
| 이자비용(*2) | 817,154 | 564,246 |
| 적격자산원가에 포함되어 있는 금액 | (155,955) | (96,216) |
25. 특수관계자
'''
        self.assertEqual(note_interest(note),(D('817154000000'),D('564246000000'),D('155955000000'),D('96216000000')))


class StubDart:
    def __init__(self,actual_interest):
        self.actual_interest=actual_interest
        self.event_calls=0
        self.event_excerpt_calls=0
    def company(self,company):return '12345678',company
    def financials(self,corp,year,basis):
        def row(name,cur,prev,sj):
            return {'sj_div':sj,'account_nm':name,'currency':'KRW','account_detail':'-',
                    'thstrm_amount':str(cur),'frmtrm_amount':str(prev),
                    'rcept_no':str(year)+'03120000000'}
        return [row('유동성차입금',100_000_000_000,100_000_000_000,'BS'),
                row('비유동성차입금',100_000_000_000,100_000_000_000,'BS'),
                row('이자비용',self.actual_interest if year==2025 else 10_000_000_000,10_000_000_000,'CF')]
    def audit_note(self,receipt,basis):return ''
    def event_filings(self,code,year,limit):
        self.event_calls+=1
        return ([{'rcept_no':'20250131000442','report_nm':'증권발행실적보고서','rcept_dt':'20250131'}],1)
    def event_excerpt(self,receipt):
        self.event_excerpt_calls+=1
        return '발행금액 3,500억원, 표면금리 3.49% (원문 대사 필요)'

class GatingCases(unittest.TestCase):
    def test_no_event_queries_before_variance_gate(self):
        d=StubDart(10_500_000_000)
        result,meta,events,explanation=analyze_company(d,'가상상장사',2025,'CFS',D('1000000000'),D('0.10'))
        self.assertFalse(result.needs_disclosure_search)
        self.assertEqual(d.event_calls,0)
        self.assertEqual(d.event_excerpt_calls,0)
        self.assertEqual(events,[])
    def test_each_or_arm_reaches_event_search(self):
        from unittest.mock import patch
        for prior,actual in ((100_000_000_000,102_000_000_000),
                             (100_000_000,110_000_000),
                             (100_000_000,90_000_000)):
            with self.subTest(prior=prior,actual=actual):
                d=StubDart(actual)
                values=(200_000_000_000,200_000_000_000,200_000_000_000,
                        prior,actual,None,None)
                with patch('main.collect_historical',return_value=(values,{'code':'12345678'})):
                    result,_,events,_=analyze_company(d,'가상상장사',2025,'CFS',D('1000000000'),D('0.10'))
                self.assertTrue(result.needs_disclosure_search)
                self.assertEqual(d.event_calls,1)
                self.assertEqual(d.event_excerpt_calls,1)
                self.assertEqual(len(events),1)

    def test_event_search_happens_only_after_gate(self):
        d=StubDart(12_000_000_000)
        result,meta,events,explanation=analyze_company(d,'가상상장사',2025,'CFS',D('1000000000'),D('0.10'))
        self.assertTrue(result.needs_disclosure_search)
        self.assertEqual(d.event_calls,1)
        self.assertEqual(len(events),1)
        self.assertIn('원인은 미확인',explanation)
        meta['year']=2025
        with TemporaryDirectory() as tmp:
            path=write_excel(Path(tmp)/'output.xlsx',result,meta,events,explanation)
            wb=load_workbook(path,read_only=True)
            self.assertEqual(wb.sheetnames,['Overall Test','관련 공시 검토'])
            self.assertEqual(wb['관련 공시 검토']['A4'].value,'증권발행실적보고서')
            rows=dict(wb['Overall Test'].iter_rows(min_row=2,values_only=True))
            self.assertEqual(rows['전기대비 절대변동률'],0.2)
            self.assertEqual(rows['금액 차이 OR 전기대비 변동 기준 충족'],'예')
            wb.close()

if __name__=='__main__':unittest.main()
