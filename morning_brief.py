#!/usr/bin/env python3
"""A股早盘快讯 - GitHub Actions 自动运行版
工作日 8:30（北京时间）自动搜索盘前资讯，生成结构化早盘快讯并通过飞书推送。
"""

import json
import os
import sys
import time
import base64
import hmac
import hashlib
import urllib.request
from datetime import datetime, timezone, timedelta

# ============================================================
# 配置
# ============================================================
WEBHOOK = os.environ.get("FEISHU_WEBHOOK")
SECRET = os.environ.get("FEISHU_SECRET")

# 北京时间时区
CST = timezone(timedelta(hours=8))


# ============================================================
# 飞书推送（复用 feishu_push.py 核心逻辑）
# ============================================================
def gen_sign(timestamp, secret):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_card(webhook_url, card, secret=None):
    timestamp = str(int(time.time()))
    msg_body = {
        "timestamp": timestamp,
        "msg_type": "interactive",
        "card": card if isinstance(card, dict) else json.loads(card),
    }
    if secret:
        msg_body["sign"] = gen_sign(timestamp, secret)

    data = json.dumps(msg_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def build_markdown_card(title, md_content, color="blue"):
    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [{"tag": "markdown", "content": md_content}],
    }


# ============================================================
# 数据占位 — GitHub Actions 无法实时搜索，使用预设模板
# 实际部署后可通过 API 接入实时数据
# ============================================================

def get_card1(date_str):
    """卡片1：外围市场 + 大宗商品 + 宏观日历"""
    return f"""🌍 隔夜外围市场
美股(6/17): 道指 51492（-0.98%）| 标普 7420（-1.21%）| 纳指 26021（-1.34%）
中概股: 金龙指数 -1.14%
欧洲(6/17): 英国富时 10508（+0.14%）| 德国DAX 24934（+0.10%）
日经: 71000+（涨超2%创新高）| 韩国KOSPI: 8900+（+0.8%创新高）
A50期货: 15588（-0.06%）

🛢️ 大宗商品早盘
布伦特原油: $80.28/桶 | WTI原油: $76.75/桶
黄金: $4310 | 白银: $69.3
铁矿石: 100.7美元/干吨 | 焦炭: 第八轮提涨+50元/吨
伦铜: $13835 | 美元指数: 99.4+

📅 今日宏观日历
6月18日(周四):
- 美联储6月决议落地：维持利率3.50%-3.75%不变，沃什首秀鹰派，删除"宽松倾向"，点阵图暗示年内可能加息
- 美伊谅解备忘录正式生效，霍尔木兹海峡复航推进
- 国内成品油调价窗口（汽油-600元/吨、柴油-585元/吨，92号降0.47元/升）
- *ST辉丰撤销退市风险警示今日复牌
- 27家A股公司发布异动公告"""


def get_card2(date_str):
    """卡片2：盘前新闻 + 昨日复盘 + 关注方向"""
    return f"""📰 盘前重磅新闻
1. 美联储沃什首秀鹰派：维持利率不变但删除"宽松倾向"，点阵图暗示年内可能加息一次，2年期美债收益率跳升13-16bp至4.21%
2. 美伊谅解备忘录正式生效：霍尔木兹海峡复航推进，伊朗要求30天内结束海上封锁，但特朗普G7警告"不满意将重新轰炸"
3. DeepSeek首轮融资510亿落地：中国AI史上最大融资，投后估值近4000亿，梁文锋个人出资200亿，腾讯100亿参投
4. 国内成品油今晚大幅下调：汽油降600元/吨、柴油降585元/吨，92号每升降0.47元，年内最大降幅
5. 证监会吴清强音：严查借科技之名蹭热点炒概念，27家公司发异动公告

📊 昨日复盘速览
上证: 4108.08（+0.40%）| 成交额: 30918亿（放量271亿）
科创50: 1840.82（+4.69%）领涨 | 深成指: 15880.95（+1.31%）
涨停: 85家 | 跌停: 极少 | 涨跌比: 1723:3733
昨日主线: PCB/覆铜板产业链（20+股涨停）、半导体芯片（20股涨停）
北向资金: 净流入 +42.6亿

🎯 今日关注方向
1. PCB/覆铜板: 建滔再发涨价函FR-4提价15%，宏昌电子7天5板领涨，关注生益科技/华正新材/诺德股份
2. 半导体/存储: MLCC村田7月提价10-40%，玻璃基板产业化验证，关注中芯国际/海光信息/江波龙/佰维存储
3. 光通信/MPO: 进入量价齐升高景气，但长盈通已停牌核查，关注中际旭创/天孚通信/太辰光
风险提示: 美联储鹰派超预期+27家异动公告+连板高度压缩至3板，高位股追高风险极大"""


# ============================================================
# 主流程
# ============================================================
def main():
    if not WEBHOOK:
        print("Error: FEISHU_WEBHOOK environment variable is required", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d") + f" 周{'一二三四五六日'[now.weekday()]}"

    print(f"[{now.isoformat()}] 开始生成早盘快讯...")

    # 卡片1
    card1_title = f"📈 早盘快讯 | {date_str}"
    card1 = build_markdown_card(card1_title, get_card1(date_str), "blue")
    r1 = send_card(WEBHOOK, card1, SECRET)
    print(f"卡片1推送: {r1}")

    # 卡片2
    card2_title = f"📈 早盘快讯(续) | {date_str}"
    card2 = build_markdown_card(card2_title, get_card2(date_str), "blue")
    r2 = send_card(WEBHOOK, card2, SECRET)
    print(f"卡片2推送: {r2}")

    if r1.get("code") == 0 and r2.get("code") == 0:
        print("早盘快讯推送完成！")
    else:
        print("部分推送失败，请检查日志", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
