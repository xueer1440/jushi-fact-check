# 据实 · 发布前事实核查（1024 侦探赛道）

**口号：发布前，先查实。**
**一句话介绍**：你要发的文案、你拿不准的说法，都能先查一遍：哪句有问题、该怎么改、真假如何，一次说清；事实结论都过十道闸、附可点开的原始来源，你能自己复核——查不到就明说查不到，不猜也不编。

- 线上可直接用：<https://jushi-fact-check.app.workbuddy.host/?sharecode=T3NGFV6FVO>
- 实时战绩（检索次数可与云端账单对账）：<https://jushi-fact-check.app.workbuddy.host/stats?sharecode=T3NGFV6FVO>
- 开发方式：WorkBuddy 辅助开发（Agent 模式，全程对话式）；**完整开发过程、踩坑与修复见 [`workbuddy.md`](workbuddy.md)**

## 快速开始

```bash
cd "D:/自动化报表/一人公司/1024侦探"

# 1) 装依赖（已装好可跳过）
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2) 配密钥：编辑 .env，填 TENCENT_SECRET_ID / TENCENT_SECRET_KEY
#    没填也能跑，会自动用演示数据（界面完整，卡片带"演示数据"角标）

# 3) 启动
./.venv/Scripts/python.exe app.py
# 打开 http://127.0.0.1:5000
```

## 页面

| 路径 | 用途 |
|---|---|
| `/` | 主页面，默认「文案体检」，可切「查一句话」 |
| `/stats` | 战绩页，现场投屏用（实时刷新） |
| `/wall` | 公开查证墙，访客能看到别人查过什么（脱敏） |
| `/q/<编号>` | 可转发核查卡（带署名二维码） |

分发链接统一带渠道参数，战绩页能看出人从哪来：`/?from=pyq`、`/?from=bossgroup`、`/?from=dm`

## 命令行

```bash
./.venv/Scripts/python.exe cli.py verify "隔夜菜亚硝酸盐超标会致癌"   # 单句深查
./.venv/Scripts/python.exe cli.py checkup "本店祖传秘方，包治颈椎病"  # 文案体检
./.venv/Scripts/python.exe cli.py regress                            # 6 例回归测试
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 模式、密钥状态、调用计数 |
| POST | `/api/verify` | `{"text": "..."}` → 完整核查结果 |
| POST | `/api/checkup` | 体检（同步返回） |
| POST | `/api/checkup/stream` | 体检（流式，`data: {json}`） |
| POST | `/api/extract` | 图片 OCR 提字（本地 RapidOCR，不传图） |
| GET | `/api/stats` | 战绩数据（含来源渠道） |
| GET | `/api/wall` | 公开查证墙数据 |
| GET | `/api/q/<code>` | 单条记录 JSON |

## 目录

```
app.py              Flask 服务
cli.py              命令行与回归入口
detective/
  config.py         配置与模式判定
  search.py         元宝搜索 SearchPro 封装
  claims.py         断言抽取、子主张拆分、类型判定
  planner.py        3 轮查询规划
  investigator.py   多轮调查主循环
  judge.py          四维判决 / 冲突检测 / 拒答闸门
  rules.py          权威度打分 + L1 风险快筛
  checkup.py        体检模式（三层漏斗）
  links.py          来源可达性检查
  demo_data.py      演示数据（真实可点开的公开页面）
  store.py          SQLite 记录与战绩
  cache.py          2 小时缓存 + IP 限流
web/                index / stats / card / wall 四个页面
regress/cases.json  6 例回归集
```

## 设计上的三条硬规矩

1. **词库只定位，结论靠检索**——竞品是"词库即结论"，所以打不过免费版；这里判定必须拿证据。
2. **结论由算法算，模型只打标签**——同输入同输出，可复现、可回归。
3. **没检索就不给结论**——体检时未深查的句子只标「未核查」，绝不标「违规」。

## 降级行为

- 无密钥 / 接口异常 / 超时 → 自动切演示数据，界面不白屏，卡片显示「演示数据」角标
- 提交参赛记录前必须确认**没有**这个角标

## 注意

- `.env` 里有密钥，已在 `.gitignore` 中，**不要提交、不要发群**
- 免费额度 6000 次/月；单句核查最多 9 次检索，一次体检最多 8 句深查（每句 ≤3 次，违禁词句 0 次）
