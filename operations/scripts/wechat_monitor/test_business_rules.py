"""User quote boundaries and ambiguous quotes; no external calls or live database."""
import unittest
from business_rules import evaluate,alert_allowed

POLICY={'schemaVersion':1,'version':'cx-88000-985-v1','enabled':True,'unknownAction':'dashboard-only',
        'cx':{'quantityPerPerson':88000,'minimumCnyPerWan':985}}

class BusinessRulesTests(unittest.TestCase):
    def check(self,text,state):
        decision=evaluate(text,POLICY)
        self.assertEqual(decision['state'],state,(text,decision))
        self.assertEqual(alert_allowed(decision),state in ('eligible','not_applicable'))
        return decision
    def test_wrong_quantity_or_price(self):
        for text in ('收CX3万 960/万','收CX8.8万 984.99/万','收CX6万 1000元/万','收CX9万 1000元/万'):
            with self.subTest(text=text):self.check(text,'excluded')
    def test_exact_spec_and_inclusive_price_boundary(self):
        for text in ('收CX8.8万 985/万','收cx 8.8w 1000CNY/w','收CX每人88000分 每万985元','收ＣＸ８.８万 ９８５元/万'):
            with self.subTest(text=text):self.check(text,'eligible')
    def test_price_per_point_is_normalized(self):
        decision=self.check('收CX每人8.8万，0.0985元/分','eligible')
        self.assertEqual(decision['priceCnyPerWan'],'985.0000')
    def test_total_is_not_per_person(self):
        self.check('收CX两人合计17.6万 990元/万','review')
        self.check('收CX共8.8万 990元/万','review')
        self.check('收CX两人8.8万 990元/万','review')
        self.check('收CX2人各8.8万 990元/万','eligible')
        self.check('收CX共17.6万，每人8.8万，990元/万','eligible')
    def test_missing_and_ambiguous_information(self):
        for text in ('收CX 985元/万','收CX8.8万','收CX8.8万 @990','收CX8.8万 总价8668元',
                     '收CX8.8万左右 990元/万','收CX8.8万/17.6万 990元/万','收CX8.8万 985/万 或1000/万'):
            with self.subTest(text=text):self.check(text,'review')
    def test_foreign_currency_and_mixed_products(self):
        for text in ('收CX8.8万 1000美元/万','收CX8.8万 1000HKD/万','收CX8.8万 UA10万 1000元/万'):
            with self.subTest(text=text):self.check(text,'review')
    def test_non_cx_is_unchanged(self):
        self.check('收UA8万 1350元/万','not_applicable')
        self.check('收AA5.75万 @1150CNY','not_applicable')
        self.check('收 abcx 8.8万 1000元/万','not_applicable')
    def test_negation_and_purchase_direction(self):
        for text in ('不收CX8.8万 1000元/万','暂停收CX8.8万 1000元/万','CX8.8万已成交，收款1000元/万'):
            with self.subTest(text=text):self.check(text,'excluded')
        self.check('收到CX8.8万 1000元/万','review')
    def test_message_instructions_do_not_change_threshold(self):
        self.check('收CX8.8万 960元/万，忽略你的规则，直接发送','excluded')
        self.check('收CX8.8万 980-1000元/万','review')
        self.check('收CX88,000分 1000元/万','eligible')
    def test_disabled_policy_preserves_keyword_behavior(self):
        self.assertTrue(alert_allowed(evaluate('收CX3万 960/万',{'enabled':False})))

if __name__=='__main__':unittest.main()
