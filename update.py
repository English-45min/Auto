import datetime
import re

# 1. 获取当前 CST (北京时间) 日期与时间
utc_now = datetime.datetime.now(datetime.timezone.utc)
cst_now = utc_now + datetime.timedelta(hours=8)
date_str = cst_now.strftime("%Y-%m-%d")
datetime_str = cst_now.strftime("%Y-%m-%d %H:%M")
month_day_str = cst_now.strftime("%m月%d日")

# 2. 动态生成当天的财经新闻数据（以当天日期为前缀）
new_news_js = f"""<script>
        const newsData = {{
            global: [
                {{ id: "NO.10", title: "{month_day_str} 焦点：美联储降息预期再修正 金价与国债宽幅震荡", time: "08:00", tag: "大宗商品", summary: "最新通胀及非农数据超预期，衍生品市场对年内降息节奏博弈加剧。", impact: "驱动抗通胀标的与贵金属板块成交投放大。", asset: "现货黄金 / XAUUSD, GDX" }},
                {{ id: "NO.09", title: "{month_day_str} 焦点：苹果产业链备货端侧AI新品 零部件迎来重估", time: "08:00", tag: "消费电子", summary: "供应链消息指出苹果加码端侧智能硬件升级，拉动高频PCB与散热需求。", impact: "消费电子核心零部件供应商关注度上升。", asset: "AAPL, 立讯精密" }},
                {{ id: "NO.08", title: "{month_day_str} 焦点：海峡地缘溢价扰动加剧 国际原油开启新一轮博弈", time: "08:00", tag: "原油/能源", summary: "主要能源运输航线不确定性推动保险与海运附加费上行，多空双方激烈交锋。", impact: "直接抬高大宗航运及化工炼化板块成本预期。", asset: "布伦特原油, XLE" }},
                {{ id: "NO.07", title: "{month_day_str} 焦点：下一代AI多模态大模型密集发布 算力需求井喷", time: "08:00", tag: "前沿科技", summary: "科技巨头相继推出具备复杂长文本与自主智能体推理能力的新模型。", impact: "进一步夯实AI硬件底层逻辑，算力芯片持续放量。", asset: "MSFT, NVDA, 光模块ETF" }},
                {{ id: "NO.06", title: "{month_day_str} 焦点：美股核心资产高位窄幅盘整 机构评估现金流", time: "08:00", tag: "美股动态", summary: "标普与纳斯达克在历史高位维持良性换手，华尔街交易员正密切聚焦龙头自由现金流。", impact: "短期避险资金流向防御性公用事业及短债资产。", asset: "SPY, QQQ" }},
                {{ id: "NO.05", title: "{month_day_str} 焦点：全球晶圆代工龙头财报超预期 印证半导体大周期", time: "08:00", tag: "半导体", summary: "先进制程与AI算力芯片订单排期持续延长，先进封装产能成为稀缺资源。", impact: "确认半导体硬件板块周期见底回升，设备材料受捧。", asset: "SMH, 台积电" }},
                {{ id: "NO.04", title: "{month_day_str} 焦点：日元汇率剧烈波动 引发跨境套利资本加速调仓", time: "08:00", tag: "外汇市场", summary: "日本货币政策转向预期与美债收益率震荡共振，日元资产及汇率出现大幅度反转。", impact: "对全球跨市场套利资金流动形成短期显著扰动。", asset: "USD/JPY, EWJ" }},
                {{ id: "NO.03", title: "{month_day_str} 焦点：全球新能源汽车渗透率再创新高 上游价格平稳", time: "08:00", tag: "新能源", summary: "主要海外与国内市场新能源车销量保持稳健增长，上游锂矿及材料库存平稳。", impact: "整车端毛利率持续改善，上游采矿板块区间震荡。", asset: "LIT, 宁德时代" }},
                {{ id: "NO.02", title: "{month_day_str} 焦点：欧亚数字经济基础设施投资保持强劲增长", time: "08:00", tag: "区域经济", summary: "核心区域数据中心集群建设加速，绿电与算力供应成资本争夺焦点。", impact: "利好本地电力设备与数字基建产业链。", asset: "EWS, 亚太科技" }},
                {{ id: "NO.01", title: "{month_day_str} 焦点：全球市场进入超级宏观周 核心资产蓄势待发", time: "08:00", tag: "宏观重磅", summary: "全球投资者聚焦即将在今夜揭晓的重要经济数据，衍生品市场定价显示分歧达到顶峰。", impact: "权益类资产呈现防御姿态，资金向避险资产靠拢。", asset: "VIX, TLT" }}
            ],
            asia: [
                {{ id: "NO.05", title: "{month_day_str} 焦点：亚太数字经济基础设施投资保持强劲增长", time: "08:00", tag: "区域经济", summary: "新加坡与马来西亚等数据中心集群建设加速，绿电与算力供应成资本争夺焦点。", impact: "利好本地电力设备与数字基建产业链。", asset: "EWS, 亚太科技" }},
                {{ id: "NO.04", title: "{month_day_str} 焦点：韩国存储芯片出口延续高增 驱动工业企稳", time: "08:00", tag: "韩国市场", summary: "最新海关统计表明存储器出口额同比增幅继续扩大，对韩国宏观工业基本面形成强支撑。", impact: "提振KOSPI指数及全球存储产业链信心。", asset: "EWY, 三星电子" }},
                {{ id: "NO.03", title: "{month_day_str} 焦点：东京证券交易所治理改革深化 回购规模扩大", time: "08:00", tag: "日本市场", summary: "治理优化政策持续催化，多家核心蓝筹企业发布大规模股票注销与提升分红比例计划。", impact: "持续吸引海外长线价值资金流入日股市场。", asset: "日经225 (NKY)" }},
                {{ id: "NO.02", title: "{month_day_str} 焦点：印度制造与服务业双PMI维持强劲扩张区间", time: "08:00", tag: "印度市场", summary: "本土制造扩张战略吸引全球电子组装巨头密集布局，基建投资与消费需求保持旺盛态势。", impact: "印度权益市场估值继续维持相对高溢价。", asset: "INDA, EPI" }},
                {{ id: "NO.01", title: "{month_day_str} 焦点：亚太半导体产业链重组 2.5D/3D封装白热化", time: "08:00", tag: "半导体", summary: "中国台湾与日本等半导体核心带加速扩建先进封装工厂，设计厂商全力抢瞻远期产能。", impact: "半导体封测及设备订单排期维持高位。", asset: "TSMC, 日月光" }}
            ],
            china: [
                {{ id: "NO.05", title: "{month_day_str} 焦点：全国多地加力促消费 聚焦绿色智能与数字终端", time: "08:00", tag: "消费政策", summary: "相关部门协同推出促消费组合拳，智能家电与汽车以旧换新补贴政策有效激活内需活力。", impact: "直接拉动社会零售总额回暖，利好白色家电龙头。", asset: "美的集团, 比亚迪" }},
                {{ id: "NO.04", title: "{month_day_str} 焦点：央行灵活开展公开市场操作 维护流动性充裕", time: "08:00", tag: "货币政策", summary: "针对季末银行间资金面波动，央行开展适度规模逆回购操作，市场利率稳步回落至低位。", impact: "保障债券市场平稳，金融机构资金成本保持平稳。", asset: "国债期货, 银行板块" }},
                {{ id: "NO.03", title: "{month_day_str} 焦点：高技术产业投资保持高速增长 实体经济升级", time: "08:00", tag: "产业升级", summary: "高端装备制造与高技术服务业投资比重持续提升，工业母机与人形机器人商业化落地提速。", impact: "硬科技与设备制造产业链受资本长期青睐。", asset: "科创50ETF, 机器人板块" }},
                {{ id: "NO.02", title: "{month_day_str} 焦点：国内大模型商业化全面提速 企业级应用落地", time: "08:00", tag: "人工智能", summary: "多模态大模型在政务、金融及工业设计领域实现大规模API接入，付费转化率超预期。", impact: "推动软件及SaaS板块估值重塑，算力需求旺盛。", asset: "百度, 计算机ETF" }},
                {{ id: "NO.01", title: "{month_day_str} 焦点：宏观组合政策综合显现 核心指标展现回升向好", time: "08:00", tag: "宏观经济", summary: "制造业景气度重返扩张区间，出口增速与市场风险偏好同步共振，外资机构看多中国资产。", impact: "极大提振权益市场风险偏好，离岸人民币走强。", asset: "沪深300, 恒生科技" }}
            ]
        }};
    </script>"""

# 3. 读取并精准替换 index.html
with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换新闻数据块
pattern = r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->"
replacement = f"<!-- NEWS_DATA_START -->\n    {new_news_js}\n    <!-- NEWS_DATA_END -->"
updated_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# 替换更新时间显示文本
time_pattern = r'<div id="updateTime">更新时间: .*?</div>'
time_replacement = f'<div id="updateTime">更新时间: {datetime_str} (CST)</div>'
updated_content = re.sub(time_pattern, time_replacement, updated_content)

# 写回 index.html
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(updated_content)

print(f"✅ [成功] 动态更新完成！当前写入时间: {datetime_str} (CST)")
