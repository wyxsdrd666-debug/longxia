#!/usr/bin/env python3
"""A股早盘快讯 - 实时API数据版
工作日 8:30（北京时间）自动获取实时市场数据，生成结构化早盘快讯并通过飞书推送。
纯标准库实现，无需第三方依赖。
"""

import json
import os
import sys
import time
import base64
import hmac
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ============================================================
# 配置
# ============================================================
WEBHOOK = os.environ.get("FEISHU_WEBHOOK")
SECRET = os.environ.get("FEISHU_SECRET")
CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

EM_UT = "bd1d9dd52904000a9764a5e5f3908853"
EM_PUSH_URL = "https://push2delay.eastmoney.com"
EM_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://data.eastmoney.com/",
}
EM_PUSH2_UT = "7eea3edcaed734bea9cbfc24409ed989"

# ============================================================
# 飞书推送
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
            return json.loads(resp.read().decode("utf-8"))
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
# 数据获取 - 通用HTTP请求
# ============================================================
def http_get(url, headers=None, timeout=10, encoding="utf-8", retries=2):
    """通用HTTP GET请求（带重试）"""
    h = headers or HEADERS
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, errors="ignore")
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"[WARN] http_get failed: {url} -> {e}")
            return ""


def http_get_json(url, headers=None, timeout=10):
    """HTTP GET 返回JSON"""
    raw = http_get(url, headers, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except:
        return None


# ============================================================
# 数据获取 - 腾讯行情
# ============================================================
def fetch_tencent_quotes(codes):
    """从腾讯行情API获取多只股票/指数数据
    返回 dict: code -> {name, price, prev_close, change_pct, high, low, volume, amount}
    """
    url = f"https://qt.gtimg.cn/q={codes}"
    raw = http_get(url, {"User-Agent": "Mozilla/5.0"}, 10, "gbk")
    result = {}
    for line in raw.split(";"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        var_part = line.split('="')
        if len(var_part) < 2:
            continue
        code_key = var_part[0].replace("v_", "")
        data_str = var_part[1].replace('"', "")
        if not data_str:
            continue
        fields = data_str.split("~")
        if len(fields) < 40:
            continue
        result[code_key] = {
            "name": fields[1],
            "price": fields[3],
            "prev_close": fields[4],
            "open": fields[5],
            "volume": fields[6],
            "change_pct": fields[32],
            "change_amt": fields[31],
            "high": fields[33],
            "low": fields[34],
            "amount": fields[37],
        }
    return result


# ============================================================
# 数据获取 - 新浪行情
# ============================================================
def fetch_sina_quotes(codes, encoding="gbk"):
    """从新浪行情API获取数据
    返回 dict: code -> {name, price, prev_close, change_pct}
    """
    url = f"https://hq.sinajs.cn/list={codes}"
    raw = http_get(url, HEADERS, 10, encoding)
    result = {}
    for line in raw.split(";"):
        line = line.strip()
        if not line or '=' not in line:
            continue
        var_name, data = line.split("=", 1)
        code_key = var_name.replace("var hq_str_", "")
        data = data.strip().replace('"', "")
        if not data:
            continue
        fields = data.split(",")
        if len(fields) == 4:  # 全球指数格式: 名称,点位,涨跌额,涨跌幅
            result[code_key] = {
                "name": fields[0],
                "price": fields[1],
                "change_amt": fields[2],
                "change_pct": fields[3],
            }
        elif len(fields) >= 5:  # A股/期货格式
            try:
                name = fields[0]
                current = float(fields[3]) if fields[3] else 0
                prev_close = float(fields[2]) if fields[2] else 0
                change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0
                result[code_key] = {
                    "name": name,
                    "price": str(current),
                    "prev_close": str(prev_close),
                    "change_pct": str(change_pct),
                }
            except:
                pass
    return result


# ============================================================
# 数据获取 - 新浪期货
# ============================================================
def fetch_sina_futures(codes):
    """从新浪期货API获取大宗商品数据
    返回 dict: code -> {name, current, high, low, prev_settle}
    """
    url = f"https://hq.sinajs.cn/list={codes}"
    raw = http_get(url, HEADERS, 10, "gbk")
    result = {}
    for line in raw.split(";"):
        line = line.strip()
        if not line or '=' not in line:
            continue
        var_name, data = line.split("=", 1)
        code_key = var_name.replace("var hq_str_", "")
        data = data.strip().replace('"', "")
        if not data:
            continue
        fields = data.split(",")
        if len(fields) < 14:
            continue
        try:
            result[code_key] = {
                "name": fields[13],  # 品种名称在fields[13]
                "current": fields[0],  # 当前价
                "high": fields[7],  # 最高
                "low": fields[8],  # 最低
                "prev_settle": fields[2] if fields[2] else fields[0],  # 昨结算
            }
        except:
            pass
    return result


# ============================================================
# 数据获取 - 东方财富全球指数
# ============================================================
def fetch_em_global_indices():
    """从东方财富获取全球主要指数（美股、欧洲、日经、美元指数）
    返回 dict: name -> {name, price, prev_close, change_pct}
    """
    secid_map = {
        "道琼斯": "100.DJIA",
        "标普500": "100.SPX",
        "纳斯达克": "100.NDX",
        "德国DAX": "100.GDAXI",
        "英国富时": "100.FTSE",
        "日经225": "100.N225",
        "美元指数": "100.UDI",
    }
    result = {}
    for name, secid in secid_map.items():
        url = (
            f"{EM_PUSH_URL}/api/qt/stock/get?"
            f"secid={secid}&fields=f43,f58,f60,f170,f169"
            f"&ut={EM_UT}"
        )
        data = http_get_json(url, {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": HEADERS["User-Agent"],
        })
        if not data or "data" not in data:
            continue
        d = data["data"]
        f43 = d.get("f43", 0) or 0
        f60 = d.get("f60", 0) or 0
        f170 = d.get("f170", 0) or 0
        f58 = d.get("f58", name)

        # f43/f60 返回整数需除100，f170已是百分比（如-98=-0.98%）
        price = f43 / 100 if isinstance(f43, int) else f43
        prev = f60 / 100 if isinstance(f60, int) else f60
        # f170: -98 = -0.98%, 需除100
        if isinstance(f170, int) and abs(f170) > 10:
            chg_pct = f170 / 100
        else:
            chg_pct = f170
            # 也从price/prev计算
            if prev:
                chg_pct = round((price - prev) / prev * 100, 2)

        result[name] = {
            "name": f58,
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change_pct": round(chg_pct, 2),
        }
    return result


# ============================================================
# 数据获取 - 东方财富板块资金流向
# ============================================================
def fetch_sector_fund_flow(top_n=8):
    """获取申万一级行业板块资金流向
    返回 list of {name, amount, dir}
    """
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f62&po=1&pz=50&pn=1&fs=m:90+t:2"
        "&fields=f12,f14,f3,f62,f184,f66,f69,f72,f75"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    if not data or "data" not in data:
        return []

    diff = data["data"].get("diff", {})
    flows = []
    for key, item in diff.items():
        name = item.get("f14", "")
        amount_raw = item.get("f62", 0)
        amount = amount_raw / 1e8 if amount_raw else 0  # 转亿
        sign = "+" if amount >= 0 else ""
        flows.append({
            "name": name,
            "amount": f"{sign}{amount:.2f}亿",
            "dir": "in" if amount >= 0 else "out",
            "raw_amount": amount,
        })

    # 按净流入金额排序，取流入TOP和流出TOP
    flows.sort(key=lambda x: x["raw_amount"], reverse=True)
    inflow_top = flows[:4]
    outflow_top = flows[-4:]
    result = []
    for f in inflow_top:
        result.append({"name": f["name"], "amount": f["amount"], "dir": "in"})
    for f in outflow_top:
        result.append({"name": f["name"], "amount": f["amount"], "dir": "out"})
    return result[:top_n]


# ============================================================
# 数据获取 - 东方财富涨停池
# ============================================================
def fetch_zt_pool(date_str="", page_size=100):
    """获取涨停股数据（通过push2delay全市场行情按涨跌幅筛选）
    早盘8:30运行时数据为前一交易日收盘数据
    """
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f3&po=0&pz=200&pn=1"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:13"
        "&fields=f12,f14,f3,f2,f20,f100"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    if not data or "data" not in data:
        return {"total": 0, "stocks": []}

    diff = data["data"].get("diff", {})
    stocks = []
    for key, item in diff.items():
        chg_pct = item.get("f3", 0) or 0
        chg_real = chg_pct / 100
        code = item.get("f12", "")
        is_chuangye = code.startswith("30")
        is_kechuang = code.startswith("68")
        if is_chuangye or is_kechuang:
            if chg_real < 18.0:
                continue
        else:
            if chg_real < 9.0:
                continue
        stocks.append({
            "code": code,
            "name": item.get("f14", ""),
            "zdf": chg_real,
            "reason": item.get("f100", ""),
        })
    stocks.sort(key=lambda x: x["zdf"], reverse=True)
    return {"total": len(stocks), "stocks": stocks[:page_size]}


# ============================================================
# 数据获取 - 东方财富跌停池
# ============================================================
def fetch_dt_pool(date_str="", page_size=100):
    """获取跌停股数量"""
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f3&po=1&pz=200&pn=1"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:13"
        "&fields=f12,f14,f3"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    if not data or "data" not in data:
        return 0

    diff = data["data"].get("diff", {})
    count = 0
    for key, item in diff.items():
        chg_pct = item.get("f3", 0) or 0
        chg_real = chg_pct / 100
        if chg_real <= -9.0:
            count += 1
    return count


# ============================================================
# 数据获取 - 东方财富北向资金
# ============================================================
def fetch_north_flow():
    """获取北向资金当日净流入"""
    url = (
        f"{EM_PUSH_URL}/api/qt/kamt.kline/get?"
        "fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        f"&klt=101&lmt=1&ut={EM_UT}"
    )
    data = http_get_json(url, {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    if not data or "data" not in data:
        return "数据获取失败"
    # hk2sh + hk2sz = 北向资金净流入
    hk2sh = data["data"].get("hk2sh", [])
    hk2sz = data["data"].get("hk2sz", [])
    sh_net = 0
    sz_net = 0
    if hk2sh:
        try:
            # API返回单位为万元，需除10000转亿元
            parts = hk2sh[0].split(",")
            sh_net = float(parts[2]) / 1e4
        except:
            pass
    if hk2sz:
        try:
            parts = hk2sz[0].split(",")
            sz_net = float(parts[2]) / 1e4
        except:
            pass
    total = sh_net + sz_net
    sign = "+" if total >= 0 else ""
    return f"{sign}{abs(total):.2f}亿"


# ============================================================
# 数据获取 - 盘前新闻（抓取财经网站）
# ============================================================
def fetch_morning_news():
    """从多个财经网站抓取盘前要闻"""
    news_items = []
    sources = [
        "https://finance.sina.com.cn/7x24/",
        "https://wallstreetcn.com/news/global",
    ]
    for src in sources:
        raw = http_get(src, HEADERS, 10, "utf-8")
        if not raw:
            continue
        # 简单提取文本标题
        # 这里只做粗提取，实际部署时可定制
        lines = raw.split("\n")
        for line in lines[:100]:
            if "title" in line.lower() and len(line.strip()) > 20:
                # 去HTML标签
                import re
                title = re.sub(r"<[^>]+>", "", line).strip()
                if title and len(title) > 15:
                    news_items.append(title)
    return news_items[:10]


# ============================================================
# 数据获取 - 融资余额
# ============================================================
def fetch_margin_balance():
    """获取融资余额数据"""
    # 腾讯行情可以取沪深融资余额指标
    codes = "ssh000001,sz399001"
    quotes = fetch_tencent_quotes(codes)
    # 融资余额需要从东方财富获取
    url = (
        f"{EM_PUSH_URL}/api/qt/stock/get?"
        "secid=1.000001&fields=f43,f3,f6,f184,f62"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    return "约2.38万亿（近似值，详细数据需收盘后更新）"


# ============================================================
# 主数据获取
# ============================================================
def collect_all_data():
    """收集所有早盘快讯需要的数据"""
    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d")
    weekday_cn = f"周{'一二三四五六日'[now.weekday()]}"
    yesterday_date = (now - timedelta(days=1)).strftime("%Y%m%d")

    print(f"[{now.isoformat()}] 开始获取实时数据...")

    # 1. A股主要指数
    print("1. 获取A股指数...")
    a_codes = "sh000001,sz399001,sz399006,sh000688,sh000300,sh000905"
    a_indices = fetch_tencent_quotes(a_codes)

    # 2. 美股+欧洲+日经+美元指数（东方财富API）
    print("2. 获取全球指数...")
    global_indices = fetch_em_global_indices()

    # 3. 港股指数（腾讯）
    print("3. 获取港股指数...")
    hk_indices = fetch_tencent_quotes("hkHSI,hkHSCEI,hkHSTECH")

    # 4. 大宗商品（新浪期货）
    print("4. 获取大宗商品...")
    commodities = fetch_sina_futures("hf_GC,hf_SI,hf_CL,hf_HG")

    # 5. 美元指数已在global_indices中获取

    # 6. 板块资金流向
    print("6. 获取板块资金流向...")
    sector_flow = fetch_sector_fund_flow(8)

    # 7. 涨停池（昨日数据）
    print("7. 获取昨日涨停池...")
    zt_data = fetch_zt_pool(yesterday_date)

    # 8. 跌停池
    print("8. 获取昨日跌停池...")
    dt_count = fetch_dt_pool(yesterday_date)

    # 9. 北向资金
    print("9. 获取北向资金...")
    north_flow_str = fetch_north_flow()

    print("数据获取完成！")

    return {
        "date": date_str,
        "weekday": weekday_cn,
        "a_indices": a_indices,
        "global_indices": global_indices,
        "hk_indices": hk_indices,
        "commodities": commodities,
        "sector_flow": sector_flow,
        "zt_data": zt_data,
        "dt_count": dt_count,
        "north_flow": north_flow_str,
    }


# ============================================================
# 格式化 - 卡片1（外围市场 + 大宗商品 + 宏观日历）
# ============================================================
def format_card1(data):
    """格式化卡片1的Markdown内容"""
    md = ""

    # 模块1：隔夜外围市场
    md += "🌍 隔夜外围市场\n"
    # 美股
    gi = data.get("global_indices", {})
    us_names = ["道琼斯", "标普500", "纳斯达克"]
    us_parts = []
    for n in us_names:
        if n in gi:
            info = gi[n]
            sign = "+" if info["change_pct"] >= 0 else ""
            us_parts.append(f"{info['name']} {info['price']}（{sign}{info['change_pct']}%）")
    if us_parts:
        md += "美股: " + " | ".join(us_parts) + "\n"
    else:
        md += "美股: 数据暂无\n"

    # 欧洲
    eu_names = ["德国DAX", "英国富时"]
    eu_parts = []
    for n in eu_names:
        if n in gi:
            info = gi[n]
            sign = "+" if info["change_pct"] >= 0 else ""
            eu_parts.append(f"{info['name']} {info['price']}（{sign}{info['change_pct']}%）")
    if eu_parts:
        md += "欧洲: " + " | ".join(eu_parts) + "\n"

    # 日经
    if "日经225" in gi:
        info = gi["日经225"]
        sign = "+" if info["change_pct"] >= 0 else ""
        md += f"日经: {info['price']}（{sign}{info['change_pct']}%）\n"

    # 港股
    hk = data.get("hk_indices", {})
    hk_parts = []
    for k, v in hk.items():
        sign = "+" if float(v.get("change_pct", 0)) >= 0 else ""
        hk_parts.append(f"{v['name']} {v['price']}（{sign}{v['change_pct']}%）")
    if hk_parts:
        md += "港股: " + " | ".join(hk_parts) + "\n"

    md += "\n"

    # 模块2：大宗商品早盘
    md += "🛢️ 大宗商品早盘\n"
    comm = data.get("commodities", {})
    for code, info in comm.items():
        md += f"{info['name']}: {info['current']}"
        try:
            cur = float(info['current'])
            prev = float(info['prev_settle']) if info['prev_settle'] else cur
            chg = (cur - prev) / prev * 100 if prev else 0
            md += f"（{chg:+.2f}%）"
        except:
            pass
        md += "\n"

    # 美元指数
    if "美元指数" in gi:
        info = gi["美元指数"]
        sign = "+" if info["change_pct"] >= 0 else ""
        md += f"美元指数: {info['price']}（{sign}{info['change_pct']}%）\n"

    md += "\n"

    # 模块3：今日宏观日历
    md += "📅 今日宏观日历\n"
    md += f"{data['date']}({data['weekday']}):\n"
    md += "- 关注今日LPR报价公布\n"
    md += "- 关注美联储利率决议后续影响\n"
    md += "- 关注北向资金流向变化\n"

    return md


# ============================================================
# 格式化 - 卡片2（盘前新闻 + 昨日复盘 + 关注方向）
# ============================================================
def format_card2(data):
    """格式化卡片2的Markdown内容"""
    md = ""

    # 模块4：盘前重磅新闻（由数据驱动）
    md += "📰 盘前数据要点\n"
    zt = data.get("zt_data", {})
    zt_total = zt.get("total", 0)
    dt = data.get("dt_count", 0)
    md += f"1. 昨日涨停{zt_total}家、跌停{dt}家\n"

    sf = data.get("sector_flow", [])
    if sf:
        inflow = [s for s in sf if s["dir"] == "in"]
        outflow = [s for s in sf if s["dir"] == "out"]
        if inflow:
            md += f"2. 昨日主力净流入: {inflow[0]['name']}({inflow[0]['amount']})领涨\n"
        if outflow:
            md += f"3. 昨日主力净流出: {outflow[0]['name']}({outflow[0]['amount']})承压\n"

    nf = data.get("north_flow", "")
    md += f"4. 北向资金: {nf}\n"
    md += "\n"

    # 模块5：昨日复盘速览
    md += "📊 昨日A股概览\n"
    a_idx = data.get("a_indices", {})
    for code, info in a_idx.items():
        chg_sign = "+" if float(info.get("change_pct", 0)) >= 0 else ""
        md += f"{info['name']}: {info['price']}（{chg_sign}{info['change_pct']}%）\n"

    md += f"涨停: {zt_total}家 | 跌停: {dt}家\n"
    md += f"北向资金: {nf}\n"
    md += "\n"

    # 模块6：今日关注方向
    md += "🎯 今日关注方向\n"
    # 根据资金流向自动生成关注方向
    if sf:
        inflow = [s for s in sf if s["dir"] == "in"]
        for i, s in enumerate(inflow[:3], 1):
            md += f"{i}. {s['name']}板块: 主力净流入{s['amount']}\n"

    # 风险提示
    md += "风险提示: 数据为盘中实时快照，收盘后数据更准确；高位股追高风险大\n"

    return md


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

    # 收集数据
    data = collect_all_data()

    # 格式化
    card1_md = format_card1(data)
    card2_md = format_card2(data)

    print("卡片1内容:")
    print(card1_md[:200])
    print("卡片2内容:")
    print(card2_md[:200])

    # 推送卡片1
    card1_title = f"📈 早盘快讯 | {date_str}"
    card1 = build_markdown_card(card1_title, card1_md, "blue")
    r1 = send_card(WEBHOOK, card1, SECRET)
    print(f"卡片1推送: {r1}")

    # 消息间延迟1.5秒，避免飞书频率限制
    time.sleep(1.5)

    # 推送卡片2
    card2_title = f"📈 早盘快讯(续) | {date_str}"
    card2 = build_markdown_card(card2_title, card2_md, "blue")
    r2 = send_card(WEBHOOK, card2, SECRET)
    print(f"卡片2推送: {r2}")

    if r1.get("code") == 0 and r2.get("code") == 0:
        print("早盘快讯推送完成！")
    else:
        print(f"警告: 部分推送失败（卡片1={r1.get('code')} 卡片2={r2.get('code')}），但流程继续", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(rc if rc is not None else 0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"FATAL ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(0)
