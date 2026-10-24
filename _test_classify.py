"""回归测试：文案分类 + 受理边界。

跑法：python _test_classify.py
（改完 claims.py / policy.py 必须跑一遍）
"""
import sys
sys.path.insert(0, '.')
from detective import claims, policy

TYPES = [
    # 服务类文案：不能当事实宣称去查
    ("我帮你把AI真正落到业务里", "service"),
    ("也直接帮你把提效工具做出来", "service"),
    ("教你的员工学会用", "service"),
    ("我能提供AI培训、提效工具定制、AI数字员工开发、AI提效诊断", "service"),
    ("专注中小企业AI落地", "service"),
    ("只服务中小企业", "service"),
    ("AI赋能顾问", "service"),
    ("做完手把手带你的团队上手", "service"),
    ("欢迎中午来吃", "service"),
    # 钩子与合规风险
    ("员工不会用、工具买了闲置", "hook"),
    ("员工不会用、工具买了闲置？", "hook"),
    ("我保证学完就能上手，包你会用", "service_guarantee"),
    ("我是高级人工智能训练师", "credential"),
    # 真宣称：必须进检索
    ("除醛率99%，母婴安全", "ad_claim"),
    ("全国销量第一", "ad_claim"),
    ("隔夜菜亚硝酸盐超标会致癌，千万别吃", "rumor"),
    ("临保车位最新是500元", "news"),
    ("某公司提供虚假检测报告骗取补贴", "rumor"),
    ("这家培训机构收了钱就跑路", "rumor"),
    ("纯天然零添加", "ad_claim"),
    ("本店祖传秘方，包治颈椎病", "ad_claim"),
]
bad = 0
for text, want in TYPES:
    got = claims.classify_claim(text)
    ok = got == want
    bad += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {text[:30]:32} -> {got:18} (期望 {want})")
print(f"\n分类器: {len(TYPES)-bad}/{len(TYPES)} 通过")

POLICY = [
    ("高血压的诊断标准是140/90mmHg", ""),
    ("2025年全国平均工资是多少", ""),
    ("我妈妈的病情严重吗", "personal"),
    ("我同事张三是不是老赖，听说欠了很多钱", "personal"),
    ("帮我查一下李四的家庭住址和电话号码", "personal"),
    ("美国下一届总统是谁", "political"),
    ("日本首相是谁", "political"),
    ("某市已取消限行政策", ""),
    ("中国移动是国企", ""),
    ("某公司被列为失信被执行人", ""),
]
pbad = 0
for text, want in POLICY:
    cat, hit = policy.check(text)
    ok = cat == want
    pbad += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {text[:30]:32} -> {cat or '(放行)':10} 命中[{hit}]")
print(f"\n边界: {len(POLICY)-pbad}/{len(POLICY)} 通过")
assert bad == 0 and pbad == 0, "有测试未通过"
