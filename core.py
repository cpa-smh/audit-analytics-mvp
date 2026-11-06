"""Pure, deterministic interest-expense overall-test calculations."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

D = Decimal


def decimal(value):
    if value is None or value == "":
        return None
    raw = str(value).strip().replace(",", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    try:
        result = D(raw)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


@dataclass(frozen=True)
class Result:
    prior_begin_debt: Optional[Decimal]
    prior_end_debt: Optional[Decimal]
    current_end_debt: Optional[Decimal]
    prior_interest: Optional[Decimal]
    actual_interest: Optional[Decimal]
    prior_capitalized: Optional[Decimal]
    current_capitalized: Optional[Decimal]
    prior_average_debt: Optional[Decimal]
    current_average_debt: Optional[Decimal]
    implied_prior_rate: Optional[Decimal]
    expected_interest: Optional[Decimal]
    variance: Optional[Decimal]
    variance_ratio: Optional[Decimal]
    tolerable_difference: Optional[Decimal]
    yoy_change: Optional[Decimal]
    yoy_change_ratio: Optional[Decimal]
    yoy_difference_threshold: Optional[Decimal]
    needs_additional_review: bool
    conclusion: str
    method: str
    warnings: tuple[str, ...]


    @property
    def needs_disclosure_search(self):
        """Legacy alias; independent disclosure collection no longer uses this flag."""
        return self.needs_additional_review


def overall_test(prior_begin_debt, prior_end_debt, current_end_debt,
                 prior_interest, actual_interest, prior_capitalized=None,
                 current_capitalized=None, min_variance_won=D('1000000000'),
                 min_variance_ratio=D('0.10')) -> Result:
    """Use prior observed interest burden to independently estimate current expense.

    Additional-review flag: abs(actual - expected) > amount OR
    abs(actual - prior actual) >= rate * abs(prior actual).
    Evaluate each available comparison independently, without dividing by zero.
    The ratios are implied average costs, NOT contractual weighted coupon rates.
    """
    nums = [decimal(x) for x in (prior_begin_debt, prior_end_debt,
              current_end_debt, prior_interest, actual_interest,
              prior_capitalized, current_capitalized)]
    pb, pe, ce, pi, ai, pc, cc = nums
    min_amt, min_pct = decimal(min_variance_won), decimal(min_variance_ratio)
    if min_amt is None or min_pct is None or min_amt < 0 or min_pct < 0:
        raise ValueError('Thresholds must be finite non-negative numbers.')
    notes = []
    prev_avg = (pb + pe) / 2 if pb is not None and pe is not None else None
    cur_avg = (pe + ce) / 2 if pe is not None and ce is not None else None
    if prev_avg is not None and prev_avg <= 0:
        prev_avg = None
        notes.append('전기 평균 이자발생부채가 0 이하입니다.')
    if cur_avg is not None and cur_avg < 0:
        cur_avg = None
        notes.append('당기 평균 이자발생부채가 음수입니다.')
    if any(x is None for x in (pb, pe, ce)):
        notes.append('차입금 잔액 중 일부가 없어 전체 기대값 계산이 불가능합니다.')
    if pi is None or ai is None:
        notes.append('전기 또는 당기 이자비용이 확인되지 않았습니다.')
    if any(x is not None and x < 0 for x in (pi, ai, pc, cc)):
        notes.append('음수 이자비용 또는 자본화 금액: 회계자료 재확인이 필요합니다.')
        prev_avg = None
    both_cap = pc is not None and cc is not None
    if not both_cap:
        notes.append('두 기간의 차입원가 자본화액이 모두 확인되지 않아 비용화 이자부담률로 근사합니다.')
    prior_basis = pi + pc if both_cap and pi is not None else pi
    rate = prior_basis / prev_avg if prior_basis is not None and prev_avg else None
    if rate is not None and rate < 0:
        rate = None
    expected_gross = rate * cur_avg if rate is not None and cur_avg is not None else None
    expected = (expected_gross - cc if both_cap else expected_gross) if expected_gross is not None else None
    if expected is not None and expected < 0:
        expected = None
        notes.append('자본화 후 기대이자비용이 음수로 계산되어 대사를 보류합니다.')
    variance = ai - expected if ai is not None and expected is not None else None
    ratio = abs(variance) / abs(expected) if variance is not None and expected not in (None, 0) else None
    tolerance = min_amt
    yoy_change = ai - pi if ai is not None and pi is not None else None
    yoy_ratio = abs(yoy_change) / abs(pi) if yoy_change is not None and pi != 0 else None
    yoy_threshold = abs(pi) * min_pct if pi is not None else None
    trigger = bool((variance is not None and abs(variance) > min_amt) or
                   (yoy_change is not None and abs(yoy_change) >= yoy_threshold))
    if expected == 0:
        notes.append('기대이자비용이 0이므로 상대차이율을 정의할 수 없습니다: 별도 검토가 필요합니다.')
    if pi == 0:
        notes.append('전기 실제 이자비용이 0이므로 전기대비 변동률은 정의되지 않지만, 변동금액 기준은 0원으로 비교합니다.')
    if trigger:
        conclusion = '금액 차이 또는 전기대비 변동 기준 충족: 추가 감사절차 검토 대상.'
    elif variance is None or yoy_change is None:
        conclusion = '계산 불가: 누락 자료를 확인한 뒤 테스트를 재수행하세요.'
    else:
        conclusion = '설정한 추가검토 기준 미충족. 공시는 별도 수집하며 감사 결론을 뜻하지 않습니다.'
    method = ('전기 자본화 전 이자비용 / 전기 평균 차입금 × 당기 평균 차입금 − 당기 자본화액'
              if both_cap else '전기 비용화 이자비용 / 전기 평균 차입금 × 당기 평균 차입금')
    notes.append('기초·기말 단순평균에 따른 추정이므로 차입·상환 시점, 환율, 변동금리 및 연결범위를 별도로 검토해야 합니다.')
    return Result(pb, pe, ce, pi, ai, pc, cc, prev_avg, cur_avg, rate,
                  expected, variance, ratio, tolerance, yoy_change, yoy_ratio,
                  yoy_threshold, trigger, conclusion,
                  method, tuple(notes))


SUBJECT_WORDS = ('차입', '사채', '차환', '상환', '회사채', '증권발행실적',
                 '증권신고서', '기업어음', '금전대차', '채무증권', '채무보증', '채무인수', '기한이익', '담보제공')


def relevant_title(title):
    return any(word in title for word in SUBJECT_WORDS)


def interpret_disclosure_coverage(result, events):
    """Disclosures are independent reference information, never a variance verdict."""
    if not events:
        return '검색 범위 내 관련 제목 공시 미확인. 차입 이슈가 없다는 의미는 아닙니다.'
    return f'차입 관련 공시 {len(events)}건. 별도 참고정보이며 차이의 원인은 미확인입니다.'
