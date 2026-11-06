import copy
import json
import unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from openpyxl import load_workbook

from audit_ai import generate_review, fallback_review, direction, STANDARD_MAP
from core import overall_test
from main import analyze_company, write_excel
from opendart import DartError
from test_core import StubDart


class AuditAICases(unittest.TestCase):
    def setUp(self):
        self.result=overall_test(100,100,100,10,12)
        self.events=[{'receipt':'20250131000442','title':'증권발행실적보고서','date':'20250131',
                     'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250131000442',
                     'quote':'발행금액 3,500억원, 표면금리 3.49%'}]
        self.valid={'procedures':[
            {'risk':'자료 신뢰성','procedure':'차입금명세와 원장을 대사한다.','evidence':'차입금명세, 원장','standard_ids':['520-5']},
            {'risk':'중복·기간귀속 가능성','procedure':'전표와 지급자료를 대사한다.','evidence':'전표, 지급자료','standard_ids':['520-7','330-A5']},
        ],'events':[{'receipt':self.events[0]['receipt'],'summary':'3,500억원 발행, 표면금리 3.49%.','consideration':'차이 원인 확정 없이 계약별 발생이자를 확인한다.'}]}

    def session(self, payload=None, status='completed'):
        session=Mock()
        session.post.return_value.json.return_value={'status':status,'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.valid if payload is None else payload)}]}]}
        return session

    def test_direction_and_different_procedures(self):
        for actual,label,keyword in ((8,'실제값 < 기대값','미계상'),(12,'실제값 > 기대값','중복'),(10,'실제값 = 기대값','일치')):
            with self.subTest(actual=actual):
                r=overall_test(100,100,100,10,actual)
                review=fallback_review(r,[])
                self.assertEqual(direction(r),label)
                self.assertIn(keyword,str(review['procedures']))
        r=overall_test(None,100,100,10,12)
        self.assertEqual(direction(r),'산정 불가')
        self.assertEqual(len(fallback_review(r,[])['procedures']),1)

    def test_no_key_does_not_call_api_or_claim_ai_output(self):
        session=Mock()
        review=generate_review(self.result,{},self.events,session=session)
        session.post.assert_not_called()
        self.assertIn('AI 생성 아님',review['status'])

    def test_structured_ai_output_preserves_calculation_and_sources(self):
        session=self.session()
        review=generate_review(self.result,{},self.events,api_key='test',session=session)
        self.assertIn('AI 생성 초안',review['status'])
        self.assertEqual(review['events'][0]['url'],self.events[0]['url'])
        self.assertIn('520-5',review['procedures'][0]['standard'])
        self.assertEqual(review['procedures'][0]['basis'],STANDARD_MAP['520-5']['summary'])
        sent=session.post.call_args.kwargs['json']
        self.assertFalse(sent['store'])
        self.assertTrue(sent['text']['format']['strict'])
        self.assertEqual(json.loads(sent['input'])['calculation']['variance'],'2.0')
        self.assertEqual(self.result.variance,D('2.0'))

    def test_invalid_citations_and_filing_ids_fall_back(self):
        for kind in ('standard','receipt','duplicate','missing','text'):
            payload=copy.deepcopy(self.valid)
            if kind=='standard':payload['procedures'][0]['standard_ids']=['999-1']
            if kind=='receipt':payload['events'][0]['receipt']='invented'
            if kind=='duplicate':payload['events']*=2
            if kind=='missing':payload['events']=[]
            if kind=='text':payload['procedures'][0]['procedure']=''
            with self.subTest(kind=kind):
                review=generate_review(self.result,{},self.events,api_key='test',session=self.session(payload))
                self.assertIn('AI 생성 실패',review['status'])
                self.assertNotIn('999-1',str(review))

    def test_api_failure_incomplete_and_refusal_are_explicit(self):
        session=self.session()
        session.post.side_effect=requests.Timeout('secret details')
        review=generate_review(self.result,{},[],api_key='secret',session=session)
        self.assertIn('AI 생성 실패',review['status'])
        self.assertNotIn('secret',str(review))
        for body in ({'status':'incomplete'}, {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]}):
            session=self.session()
            session.post.return_value.json.return_value=body
            self.assertIn('AI 생성 실패',generate_review(self.result,{},[],api_key='test',session=session)['status'])

    def test_disclosure_failure_is_not_no_disclosures(self):
        dart=StubDart(10_000_000_000)
        dart.event_filings=Mock(side_effect=DartError('unavailable'))
        _,info,events,explanation=analyze_company(dart,'회사',2025,'CFS',D('1000000000'),D('.10'))
        self.assertEqual(events,[])
        self.assertEqual(info['disclosure_status'],'검색 실패')
        self.assertIn('공시 없음으로 해석할 수 없습니다',explanation)

    def test_workbook_with_mocked_ai_has_citations_links_and_safe_text(self):
        result,info,events,explanation=analyze_company(StubDart(12_000_000_000),'회사',2025,'CFS',D('1000000000'),D('.10'))
        review=generate_review(result,info,events,api_key='test',session=self.session())
        review['procedures'][0]['procedure']='=1+1'
        with TemporaryDirectory() as tmp:
            path=write_excel(Path(tmp)/'review.xlsx',result,info,events,explanation,review)
            wb=load_workbook(path)
            self.assertEqual(wb.sheetnames,['이자비용 차이 분석','추가 감사절차','차입 관련 공시'])
            self.assertEqual(wb['추가 감사절차']['B5'].data_type,'s')
            self.assertIn('520-5',wb['추가 감사절차']['D5'].value)
            self.assertTrue(wb['추가 감사절차']['G5'].hyperlink.target.startswith('https://www.kicpa.or.kr'))
            self.assertEqual(wb['차입 관련 공시']['D5'].hyperlink.target,events[0]['url'])
            self.assertIn('3,500',wb['차입 관련 공시']['F5'].value)
            self.assertEqual(wb['차입 관련 공시'].freeze_panes,'B5')
            wb.close()


if __name__=='__main__':unittest.main()
