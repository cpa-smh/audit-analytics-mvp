"""Regression checks: the application must analyze interest expense only."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal
from openpyxl import load_workbook
from main import ACCOUNTS, demo, analyze, export
from ai import Account, Answer

class InterestOnlyTests(unittest.TestCase):
    def test_one_account_configuration(self):
        self.assertEqual(ACCOUNTS, ('이자비용',))
        self.assertEqual(list(Account.model_fields['account'].annotation.__args__), ['이자비용'])
    def test_demo_one_interest_row(self):
        company,values,sources,warnings=demo(2025,'CFS')
        self.assertEqual(list(values), ['이자비용'])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['account'], '이자비용')
    def test_export_has_interest_only(self):
        company,values,sources,warnings=demo(2025,'CFS')
        class Args:
            year=2025
            basis='CFS'
            pct=Decimal('.20')
            amount=Decimal('1000000000')
        reports=analyze(Args,values,sources,None,'AI 미사용')
        self.assertEqual(len(reports),1)
        with TemporaryDirectory() as d:
            p=Path(d)/'test.xlsx'
            export(p,company,2025,reports,sources,warnings)
            wb=load_workbook(p,read_only=True)
            self.assertEqual(wb.sheetnames,['분석적절차 결과','관련 공시 근거'])
            self.assertEqual(wb.worksheets[0]['A4'].value,'이자비용')

if __name__=='__main__':
    unittest.main()
