import os, sys
os.environ["RUN_MODE"] = "demo"
sys.path.insert(0, '.')
from detective import checkup

CASES = [
    ("实体店海报（应判高危）",
     "本店祖传秘方，纯天然零添加，包治颈椎病，一次见效，全网最低价，销量第一。老客户都说好，常年回头客不断。"),
    ("产品功效（应有风险）",
     "这款除醛喷雾采用日本进口技术，24小时除醛率99%，母婴安全，新房入住必备。"),
    ("正常文案（应判可发布）",
     "我们是一家开了八年的小面馆，牛肉每天早市现买，汤底熬六小时，人均25元，欢迎中午来吃。"),
    ("服务承诺（应不再误杀）",
     "我帮你把AI真正落到业务里，教你员工学会用，也帮你把提效工具做出来。"),
    ("效果保证（应按法规提示、不检索）",
     "保证学完就能上手，包你会用，无效退款。"),
]

for name, text in CASES:
    r = checkup.run_checkup(text, use_llm=False)
    c = r["counts"]
    print(f"[{name}] {r['grade']}（{r['score']}分） 高危{c['high']} 需改{c['mid']} 违禁{c['lex']} 待核{c['pending']} 检索{r['searches']}")
    for it in r["items"][:4]:
        print(f"    - [{it['risk_level']}] {it['sentence'][:26]} | {it['issue_type']}")
    print(f"    结语: {r['headline']}")
    print()
