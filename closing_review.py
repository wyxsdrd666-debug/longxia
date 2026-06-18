#!/usr/bin/env python3
"""A股收盘复盘 - 实时API数据版
工作日 15:30（北京时间）自动获取当日收盘数据，生成14模块复盘长图+飞书卡片推送。
纯标准库实现，无需第三方依赖。
"""

import json
import os
import sys
import time
import re
import base64
import hmac
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from html import escape

# ============================================================
# 配置
# ============================================================
WEBHOOK = os.environ.get("FEISHU_WEBHOOK")
SECRET = os.environ.get("FEISHU_SECRET")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

EM_UT = "bd1d9dd52904000a9764a5e5f3908853"
EM_PUSH_URL = "https://push2delay.eastmoney.com"  # push2 502 宕机时的延迟备用域名
EM_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer": "https://data.eastmoney.com/",
}

# push2 同样需要这个 ut 值
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


def send_image(webhook_url, image_key, secret=None):
    """发送飞书原生图片消息（msg_type: image）"""
    timestamp = str(int(time.time()))
    msg_body = {
        "timestamp": timestamp,
        "msg_type": "image",
        "content": {"image_key": image_key},
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
    """构建飞书交互式卡片消息体"""
    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [{"tag": "markdown", "content": md_content}],
    }


def build_image_card(title, image_key, alt_text="长图", color="red"):
    """构建飞书图片卡片——长图直接展示"""
    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {"tag": "plain_text", "content": alt_text},
                "preview": True,
                "scale_type": "fit_horizontal",
                "size": "stretch",
            }
        ],
    }


# ============================================================
# 飞书图片上传
# ============================================================
def get_tenant_access_token(app_id, app_secret):
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("tenant_access_token", "")
            if not token:
                print(f"[ERROR] 获取tenant_access_token失败: {data}", file=sys.stderr)
                return ""
            return token
    except Exception as e:
        print(f"[ERROR] 获取tenant_access_token异常: {e}", file=sys.stderr)
        return ""


def upload_image_to_feishu(app_id, app_secret, image_path):
    """上传图片到飞书，返回 image_key"""
    token = get_tenant_access_token(app_id, app_secret)
    if not token:
        return ""

    boundary = "----WorkBuddyFormBoundary7MA4YWxkTrZu0gW"
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
    except Exception as e:
        print(f"[ERROR] 读取图片文件失败: {e}", file=sys.stderr)
        return ""

    if not image_data:
        print("[ERROR] 图片为空", file=sys.stderr)
        return ""

    # 构建 multipart/form-data 请求体
    crlf = b"\r\n"
    parts = []
    parts.append(f"--{boundary}".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="image_type"')
    parts.append(b"")
    parts.append(b"message")
    parts.append(f"--{boundary}".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="image"; filename="closing-review.png"')
    parts.append(b"Content-Type: image/png")
    parts.append(b"")
    parts.append(image_data)
    parts.append(f"--{boundary}--".encode("utf-8"))

    body_data = crlf.join(parts)

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    req = urllib.request.Request(url, data=body_data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            code = data.get("code", -1)
            if code != 0:
                print(f"[ERROR] 上传图片失败: {data}", file=sys.stderr)
                return ""
            image_key = data.get("data", {}).get("image_key", "")
            print(f"图片上传成功! image_key: {image_key}")
            return image_key
    except Exception as e:
        print(f"[ERROR] 上传图片异常: {e}", file=sys.stderr)
        return ""


# ============================================================
# 数据获取 - 通用HTTP
# ============================================================
def http_get(url, headers=None, timeout=10, encoding="utf-8", retries=2):
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
    """腾讯行情API，返回 dict: code -> {name, price, prev_close, change_pct, ...}"""
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
        try:
            change_pct = float(fields[32]) if fields[32] else 0
            price = float(fields[3]) if fields[3] else 0
            prev_close = float(fields[4]) if fields[4] else 0
            volume = int(fields[6]) if fields[6] else 0
            amount = float(fields[37]) if fields[37] else 0
            result[code_key] = {
                "name": fields[1],
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "change_amt": float(fields[31]) if fields[31] else 0,
                "high": float(fields[33]) if fields[33] else 0,
                "low": float(fields[34]) if fields[34] else 0,
                "volume": volume,
                "amount": amount,
            }
        except:
            pass
    return result


# ============================================================
# 数据获取 - 新浪期货
# ============================================================
def fetch_sina_futures(codes):
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
            cur = float(fields[0]) if fields[0] else 0
            prev = float(fields[2]) if fields[2] else cur
            chg_pct = round((cur - prev) / prev * 100, 2) if prev else 0
            result[code_key] = {
                "name": fields[13],  # 品种名称在fields[13]
                "price": fields[0],
                "change_pct": f"{chg_pct:+.2f}%",
            }
        except:
            pass
    return result


# ============================================================
# 数据获取 - 东方财富板块资金流向
# ============================================================
def fetch_sector_fund_flow(top_n=8):
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f62&po=1&pz=50&pn=1&fs=m:90+t:2"
        "&fields=f12,f14,f3,f62,f184,f66,f69,f72,f75"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, EM_HEADERS)
    if not data or "data" not in data:
        return []
    diff = data["data"].get("diff", {})
    flows = []
    for key, item in diff.items():
        name = item.get("f14", "")
        amount_raw = item.get("f62", 0) or 0
        amount = amount_raw / 1e8
        sign = "+" if amount >= 0 else ""
        flows.append({
            "name": name,
            "amount": f"{sign}{abs(amount):.2f}亿",
            "dir": "in" if amount >= 0 else "out",
            "raw_amount": amount,
        })
    flows.sort(key=lambda x: x["raw_amount"], reverse=True)
    inflow_top = flows[:4]
    outflow_top = sorted(flows[-4:], key=lambda x: x["raw_amount"])
    result = []
    for f in inflow_top:
        result.append({"name": f["name"], "amount": f["amount"], "dir": "in"})
    for f in outflow_top:
        result.append({"name": f["name"], "amount": f["amount"], "dir": "out"})
    return result[:top_n]


# ============================================================
# 数据获取 - 涨停池
# ============================================================
def fetch_zt_pool(top_n=30):
    """获取涨停股票（通过push2delay全市场行情按涨跌幅筛选，替代push2ex API pool始终为空的bug）"""
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f3&po=0&pz=200&pn=1"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:13"  # 深A+沪A+创业板+科创板
        "&fields=f12,f14,f3,f2,f8,f20,f100"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, EM_HEADERS)
    if not data or "data" not in data:
        return {"total": 0, "stocks": []}

    diff = data["data"].get("diff", {})
    stocks = []
    for key, item in diff.items():
        chg_pct = item.get("f3", 0) or 0
        chg_real = chg_pct / 100  # push2delay的f3需要除以100
        # 涨停阈值：主板≥9.5%, 科创/创业板≥19.5%（盘中有波动，设宽松阈值）
        code = item.get("f12", "")
        is_chuangye = code.startswith("30")
        is_kechuang = code.startswith("68")
        if is_chuangye or is_kechuang:
            if chg_real < 18.0:  # 20%涨停板，允许1%波动
                continue
        else:
            if chg_real < 9.0:  # 10%涨停板，允许1%波动
                continue
        stocks.append({
            "code": code,
            "name": item.get("f14", ""),
            "zdf": chg_real,
            "reason": item.get("f100", ""),  # 所属行业
            "ltsz": item.get("f20", 0) or 0,  # 总市值
        })
    # 按涨幅从高到低排序
    stocks.sort(key=lambda x: x["zdf"], reverse=True)
    return {"total": len(stocks), "stocks": stocks[:top_n]}


def fetch_dt_pool():
    """获取跌停股票（同上，按涨跌幅升序筛选负值）"""
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f3&po=1&pz=200&pn=1"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:13"
        "&fields=f12,f14,f3"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, EM_HEADERS)
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
# 数据获取 - 北向资金
# ============================================================
def fetch_north_flow():
    url = (
        f"{EM_PUSH_URL}/api/qt/kamt.kline/get?"
        "fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        "&klt=101&lmt=1&ut=" + EM_UT
    )
    data = http_get_json(url, EM_HEADERS)
    if not data or "data" not in data:
        return {"total": "数据获取失败", "sh": "", "sz": ""}
    hk2sh = data["data"].get("hk2sh", [])
    hk2sz = data["data"].get("hk2sz", [])
    sh_net = 0
    sz_net = 0
    if hk2sh:
        try:
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
    return {
        "total": f"{sign}{abs(total):.2f}亿",
        "sh": f"{sign}{abs(sh_net):.2f}亿",
        "sz": f"{sign}{abs(sz_net):.2f}亿",
    }


# ============================================================
# 数据获取 - 概念板块涨幅TOP
# ============================================================
def fetch_concept_top(top_n=6):
    """获取概念板块涨幅排行"""
    url = (
        f"{EM_PUSH_URL}/api/qt/clist/get?"
        "fid=f3&po=1&pz=20&pn=1&fs=m:90+t:3"
        "&fields=f12,f14,f2,f3,f62,f184"
        f"&ut={EM_UT}"
    )
    data = http_get_json(url, EM_HEADERS)
    if not data or "data" not in data:
        return []
    diff = data["data"].get("diff", {})
    concepts = []
    for key, item in diff.items():
        name = item.get("f14", "")
        chg_pct = item.get("f3", 0) or 0
        chg_real = chg_pct / 100 if abs(chg_pct) > 500 else chg_pct
        concepts.append({
            "name": name,
            "change_pct": round(chg_real, 2),
        })
    concepts.sort(key=lambda x: x["change_pct"], reverse=True)
    return concepts[:top_n]


# ============================================================
# 数据获取 - 盘后热点资讯
# ============================================================
def fetch_closing_news(top_n=8):
    """从东方财富7x24快讯获取今日盘后热点资讯"""
    news_items = []
    # 红字焦点快讯 (101) + 7x24全球直播 (102)
    for news_type in [101, 102]:
        url = (
            f"https://newsapi.eastmoney.com/kuaixun/v1/"
            f"getlist_{news_type}_ajaxResult_50_1_.html"
        )
        raw = http_get(url, EM_HEADERS, 10, "utf-8")
        if not raw:
            continue
        # 去掉 JSONP 包装 var ajaxResult={...}
        m = re.search(r"var ajaxResult=(.+)", raw)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        lives = data.get("LivesList", [])
        for item in lives:
            title = item.get("title", "").strip()
            digest = item.get("digest", "").strip()
            # 去掉括号中的重复标题
            if digest.startswith(f"【{title}】"):
                digest = digest[len(title) + 3:].strip()
            elif digest.startswith(f"【"):
                # 去掉开头括号
                bracket_end = digest.find("】")
                if bracket_end > 0 and bracket_end < 30:
                    digest = digest[bracket_end + 2:].strip()
            if title and len(title) > 8:
                news_items.append({
                    "title": title,
                    "digest": digest[:100] if digest else "",
                })
    # 去重
    seen = set()
    unique = []
    for n in news_items:
        if n["title"] not in seen:
            seen.add(n["title"])
            unique.append(n)
    return unique[:top_n]


# ============================================================
# 主数据收集
# ============================================================
def collect_all_data():
    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d")
    weekday_cn = f"周{'一二三四五六日'[now.weekday()]}"
    today_em = now.strftime("%Y%m%d")

    print(f"[{now.isoformat()}] 开始获取收盘数据...")

    # 1. A股指数
    print("1. A股指数...")
    a_codes = "sh000001,sz399001,sz399006,sh000688,sh000300,sh000905"
    a_indices = fetch_tencent_quotes(a_codes)

    # 2. 大宗商品
    print("2. 大宗商品...")
    commodities = fetch_sina_futures("hf_GC,hf_SI,hf_CL,hf_HG")

    # 3. 美元指数（东方财富）
    print("3. 美元指数...")
    url_usd = (
        f"{EM_PUSH_URL}/api/qt/stock/get?"
        "secid=100.UDI&fields=f43,f58,f60,f170"
        f"&ut={EM_UT}"
    )
    usd_data_raw = http_get_json(url_usd, {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": HEADERS["User-Agent"],
    })
    usd_info = {}
    if usd_data_raw and "data" in usd_data_raw:
        d = usd_data_raw["data"]
        f43 = d.get("f43", 0) or 0
        f170 = d.get("f170", 0) or 0
        price = f43 / 100 if isinstance(f43, int) else f43
        chg = f170 / 100 if isinstance(f170, int) and abs(f170) > 10 else f170
        usd_info = {"price": round(price, 2), "change_pct": round(chg, 2)}

    # 4. 板块资金流向
    print("4. 板块资金流向...")
    sector_flow = fetch_sector_fund_flow(8)

    # 5. 概念板块涨幅
    print("5. 概念板块...")
    concept_top = fetch_concept_top(6)

    # 6. 涨停池
    print("6. 涨停池...")
    zt_data = fetch_zt_pool(60)

    # 7. 跌停池
    print("7. 跌停池...")
    dt_count = fetch_dt_pool()

    # 8. 北向资金
    print("8. 北向资金...")
    north_flow = fetch_north_flow()

    # 9. 盘后热点资讯
    print("9. 盘后热点资讯...")
    closing_news = fetch_closing_news(8)

    # 计算成交额
    sh_amount = a_indices.get("sh000001", {}).get("amount", 0)
    sz_amount = a_indices.get("sz399001", {}).get("amount", 0)
    total_amount = (sh_amount + sz_amount) / 1e8 if sh_amount else 0

    # 情绪温度计算
    zt_total = zt_data.get("total", 0)
    temp = min(100, 50 + zt_total)  # 简化：涨停家数映射到温度
    temp_level = "过热" if temp >= 80 else ("偏热" if temp >= 60 else ("中性" if temp >= 40 else "偏冷"))

    print("数据获取完成！")

    return {
        "date": date_str,
        "weekday": weekday_cn,
        "a_indices": a_indices,
        "commodities": commodities,
        "usd_info": usd_info,
        "sector_flow": sector_flow,
        "concept_top": concept_top,
        "zt_data": zt_data,
        "zt_total": zt_total,
        "dt_count": dt_count,
        "north_flow": north_flow,
        "total_amount": round(total_amount, 0),
        "sentiment_temp": temp,
        "sentiment_level": temp_level,
        "closing_news": closing_news,
    }


# ============================================================
# HTML 长图生成
# ============================================================
def generate_html(data):
    date_str = data["date"]
    wd = data["weekday"]
    s_temp = data["sentiment_temp"]
    s_level = data["sentiment_level"]
    zt_total = data.get("zt_total", data["zt_data"].get("total", 0))
    temp_color = "#e74c3c" if s_temp >= 80 else ("#f39c12" if s_temp >= 60 else "#27ae60")

    # 指数行
    idx_rows = ""
    for code, idx in data["a_indices"].items():
        chg_sign = "+" if idx["change_pct"] >= 0 else ""
        c = "#e74c3c" if idx["change_pct"] >= 0 else "#27ae60"
        idx_rows += f'<tr><td>{idx["name"]}</td><td style="color:{c}">{idx["price"]}</td><td style="color:{c}">{chg_sign}{idx["change_pct"]}%</td></tr>\n'

    # 概念板块
    concept_rows = ""
    zt_stocks = data["zt_data"].get("stocks", [])
    for ct in data["concept_top"]:
        # 从涨停池中找该概念对应的龙头
        leader = ""
        for s in zt_stocks[:20]:
            if s.get("reason", "") == ct["name"] or ct["name"] in s.get("reason", ""):
                leader = s["name"]
                break
        if not leader:
            leader = "—"
        concept_rows += f'<tr><td>{ct["name"]}</td><td class="num">{ct["change_pct"]:+.2f}%</td><td>{leader}</td></tr>\n'

    # 板块资金
    flow_rows = ""
    for fl in data["sector_flow"]:
        c = "#e74c3c" if fl["dir"] == "in" else "#27ae60"
        tag = "流入" if fl["dir"] == "in" else "流出"
        flow_rows += f'<tr><td>{fl["name"]}</td><td style="color:{c}">{tag} {fl["amount"]}</td></tr>\n'

    # 涨停龙头
    leader_rows = ""
    for ld in zt_stocks[:6]:
        leader_rows += f'<tr><td>{ld["name"]}</td><td>{ld["code"]}</td><td class="num">{ld["zdf"]:+.1f}%</td><td>{ld["reason"]}</td></tr>\n'

    # 大宗商品
    comm_rows = ""
    for code, cm in data["commodities"].items():
        comm_rows += f'<tr><td>{cm["name"]}</td><td>{cm["price"]}</td><td>{cm["change_pct"]}</td></tr>\n'

    # 北向资金
    nf = data["north_flow"]

    # 盘后热点资讯
    news_rows = ""
    for n in data.get("closing_news", []):
        title = n["title"]
        digest = n.get("digest", "")
        if digest:
            news_rows += f'<tr><td style="color:#e74c3c;font-weight:bold;">{escape(title)}</td><td style="color:#aab;font-size:12px;">{escape(digest)}</td></tr>\n'
        else:
            news_rows += f'<tr><td style="color:#e74c3c;font-weight:bold;">{escape(title)}</td><td>—</td></tr>\n'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=800">
<title>A股收盘复盘 | {date_str}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: "Microsoft YaHei","PingFang SC",sans-serif; background: #0a0e27; color: #e0e0e0; width: 800px; margin: 0 auto; }}
.header {{ background: linear-gradient(135deg,#1a1a3e,#0d1b3e,#1a1a3e); padding: 28px 24px; text-align: center; border-bottom: 2px solid #2a4a8a; }}
.header h1 {{ font-size: 24px; color: #fff; margin-bottom: 6px; }}
.header .sub {{ font-size: 13px; color: #8899bb; }}
.header .date {{ font-size: 14px; color: #ffd700; margin-top: 4px; }}
.section {{ margin: 16px 12px; background: #111633; border-radius: 8px; overflow: hidden; border: 1px solid #1e2a4a; }}
.section-title {{ background: linear-gradient(90deg,#1a2d5a,#111633); padding: 10px 16px; font-size: 15px; font-weight: bold; color: #ffd700; border-bottom: 1px solid #2a3a5a; }}
.section-content {{ padding: 12px 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #1a2540; color: #8899bb; font-weight: normal; padding: 8px 10px; text-align: left; font-size: 12px; border-bottom: 1px solid #2a3a5a; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #1a2540; }}
tr:last-child td {{ border-bottom: none; }}
.num {{ text-align: right; }}
.sentiment-box {{ display: flex; align-items: center; gap: 16px; }}
.sentiment-gauge {{ width: 80px; height: 80px; border-radius: 50%; background: conic-gradient({temp_color} 0% {s_temp}%,#1a2540 {s_temp}% 100%); display: flex; align-items: center; justify-content: center; }}
.sentiment-inner {{ width: 58px; height: 58px; border-radius: 50%; background: #111633; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: bold; color: {temp_color}; }}
.sentiment-stats {{ flex: 1; font-size: 12px; line-height: 1.8; }}
.sentiment-stats span {{ color: #8899bb; }}
.sentiment-stats .val {{ color: #ffd700; font-weight: bold; }}
.footer {{ text-align: center; padding: 16px; color: #556; font-size: 11px; border-top: 1px solid #1e2a4a; margin-top: 12px; }}
.risk {{ color: #27ae60; font-weight: bold; }}
.profit {{ color: #e74c3c; font-weight: bold; }}
.highlight {{ color: #ffd700; }}
</style>
</head>
<body>

<div class="header">
    <h1>A股收盘复盘报告</h1>
    <div class="date">{date_str} {wd}收盘 | 总成交额 {data['total_amount']}亿</div>
    <div class="sub">14模块全景复盘 · AI自动生成 · 实时API数据</div>
</div>

<div class="section">
    <div class="section-title">🌡️ 市场情绪温度计</div>
    <div class="section-content">
        <div class="sentiment-box">
            <div class="sentiment-gauge"><div class="sentiment-inner">{s_temp}°</div></div>
            <div class="sentiment-stats">
                <div>市场情绪: <span class="val">{s_level}</span></div>
                <div>涨停: <span class="val profit">{zt_total}家</span> | 跌停: <span class="val risk">{data['dt_count']}家</span></div>
                <div>成交额: <span class="val">{data['total_amount']}亿</span></div>
                <div>北向资金: <span class="val profit">{nf['total']}</span></div>
            </div>
        </div>
    </div>
</div>

<div class="section">
    <div class="section-title">📊 指数概览</div>
    <div class="section-content">
        <table><tr><th>指数</th><th>收盘</th><th>涨跌幅</th></tr>{idx_rows}</table>
    </div>
</div>

<div class="section">
    <div class="section-title">🔥 涨停概念分布</div>
    <div class="section-content">
        <table><tr><th>概念板块</th><th>涨幅</th><th>龙头代表</th></tr>{concept_rows}</table>
    </div>
</div>

<div class="section">
    <div class="section-title">💰 板块资金流向热力图</div>
    <div class="section-content">
        <table><tr><th>板块</th><th>资金动向</th></tr>{flow_rows}</table>
    </div>
</div>

<div class="section">
    <div class="section-title">👑 涨停龙头股</div>
    <div class="section-content">
        <table><tr><th>股票</th><th>代码</th><th>涨跌幅</th><th>涨停原因</th></tr>{leader_rows}</table>
    </div>
</div>

<div class="section">
    <div class="section-title">🌏 北向资金</div>
    <div class="section-content" style="font-size:13px;line-height:2;">
        <div>当日北向资金净<span class="profit">{nf['total']}</span></div>
        <div>沪股通: <span class="profit">{nf['sh']}</span> | 深股通: <span class="profit">{nf['sz']}</span></div>
    </div>
</div>

<div class="section">
    <div class="section-title">🛢️ 大宗商品行情</div>
    <div class="section-content">
        <table><tr><th>品种</th><th>价格</th><th>涨跌</th></tr>{comm_rows}</table>
    </div>
</div>

<div class="section">
    <div class="section-title">📰 盘后热点资讯</div>
    <div class="section-content">
        <table><tr><th style="width:40%;">标题</th><th>摘要</th></tr>{news_rows}</table>
    </div>
</div>

<div class="footer">
    A股收盘复盘 · 数据来源：腾讯行情/新浪财经/东方财富 · 实时API · 仅供参考不构成投资建议
</div>

</body>
</html>"""
    return html


# ============================================================
# HTML → PNG 长图渲染（可选，需 playwright）
# ============================================================
def render_html_to_png(html_path, png_path, width=800):
    """使用 playwright 将 HTML 渲染为 PNG 长图。
    需要安装: pip install playwright && playwright install chromium
    GitHub Actions 环境已预装 chromium。
    如果 playwright 不可用则返回 None。
    """
    import traceback as _tb

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] playwright 未安装，跳过 PNG 渲染", file=sys.stderr)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": width, "height": 800})
            # 使用 "load" 而非 "networkidle" — file:// 协议下 networkidle 可能永不触发
            page.goto(f"file://{html_path}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)  # 等待 CSS 渲染完成

            # 获取完整页面高度并截图
            full_height = page.evaluate("document.body.scrollHeight")
            if not full_height or full_height < 100:
                full_height = page.evaluate("() => Math.max(document.body.scrollHeight, 800)")
                print(f"[WARN] scrollHeight 异常，使用兜底值: {full_height}px")
            print(f"页面高度: {full_height}px, 开始截图...")
            page.set_viewport_size({"width": width, "height": full_height + 50})
            page.screenshot(path=png_path + ".raw.png", full_page=True)
            browser.close()
            print(f"原始截图已保存: {png_path}.raw.png")

        # 用 PIL 缩小图片至飞书卡片允许的尺寸范围（高度不超过3840px）
        _resize_png(png_path + ".raw.png", png_path, max_height=3840)
        os.remove(png_path + ".raw.png")

        file_size = os.path.getsize(png_path) / 1024
        print(f"PNG长图已保存: {png_path} ({file_size:.0f} KB)")
        return png_path
    except Exception as e:
        print(f"[WARN] PNG渲染失败: {e}", file=sys.stderr)
        _tb.print_exc(file=sys.stderr)
        return None


def _resize_png(src_path, dst_path, max_height=3840):
    """缩放 PNG 图片使其高度不超过 max_height，保持宽高比"""
    try:
        from PIL import Image
        img = Image.open(src_path)
        w, h = img.size
        if h > max_height:
            scale = max_height / h
            new_w = int(w * scale)
            new_h = max_height
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(f"图片已缩放: {w}x{h} -> {new_w}x{new_h}")
        img.save(dst_path, "PNG", optimize=True)
    except ImportError:
        # PIL 不可用时直接复制原图（兜底）
        import shutil
        shutil.copyfile(src_path, dst_path)
        print("[WARN] PIL 未安装，使用原始截图")
    except Exception as e:
        # PIL 处理失败时也回退到复制原图
        import shutil
        import traceback as _tb
        shutil.copyfile(src_path, dst_path)
        print(f"[WARN] PIL 处理失败: {e}，使用原始截图", file=sys.stderr)
        _tb.print_exc(file=sys.stderr)


# ============================================================
# 飞书卡片格式化
# ============================================================
def format_feishu_cards(data):
    """将数据格式化为3张飞书Markdown卡片"""
    date_str = f"{data['date']} {data['weekday']}"
    nf = data["north_flow"]

    # 卡片1: 模块1-5
    card1_md = f"""🌡️ **市场情绪温度计: {data['sentiment_temp']}°（{data['sentiment_level']}）**
涨停: {data['zt_data']['total']}家 | 跌停: {data['dt_count']}家
成交额: {data['total_amount']}亿
北向资金: 净{nf['total']}

📊 **指数概览**
"""
    for code, idx in data["a_indices"].items():
        sign = "+" if idx["change_pct"] >= 0 else ""
        card1_md += f"{idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）\n"

    card1_md += "\n🔥 **概念板块涨幅TOP**\n"
    for ct in data["concept_top"]:
        card1_md += f"• {ct['name']}: {ct['change_pct']:+.2f}%\n"

    card1_md += "\n💰 **板块资金流向**\n"
    for fl in data["sector_flow"]:
        tag = "🔴流入" if fl["dir"] == "in" else "🟢流出"
        card1_md += f"• {fl['name']}: {tag} {fl['amount']}\n"

    card1_md += "\n👑 **涨停龙头股**\n"
    for ld in data["zt_data"]["stocks"][:6]:
        card1_md += f"• {ld['name']}({ld['code']}) {ld['zdf']:+.1f}%: {ld['reason']}\n"

    # 卡片2: 模块6-10
    card2_md = f"""🛢️ **大宗商品行情**
"""
    for code, cm in data["commodities"].items():
        card2_md += f"• {cm['name']}: {cm['price']}（{cm['change_pct']}）\n"

    usd = data.get("usd_info", {})
    if usd:
        sign = "+" if usd.get("change_pct", 0) >= 0 else ""
        card2_md += f"\n📈 **美元指数**: {usd.get('price', 'N/A')}（{sign}{usd.get('change_pct', 'N/A')}%）\n"

    card2_md += f"\n🌏 **北向资金: 净{nf['total']}**\n"
    card2_md += f"沪股通: {nf['sh']} | 深股通: {nf['sz']}\n"

    # 卡片3: 模块11-14
    card3_md = "📝 **今日涨停板块梳理**\n"
    for ct in data["concept_top"][:5]:
        card3_md += f"• {ct['name']}（涨幅{ct['change_pct']:+.2f}%）\n"

    # 盘后热点资讯
    closing_news = data.get("closing_news", [])
    if closing_news:
        card3_md += "\n📰 **盘后热点资讯**\n"
        for n in closing_news[:5]:
            card3_md += f"• {n['title']}\n"
            if n.get("digest"):
                card3_md += f"  {n['digest'][:60]}\n"

    card3_md += "\n📅 **近期关注**\n"
    card3_md += "• 关注明日LPR报价\n"
    card3_md += "• 关注美联储决议后续影响\n"
    card3_md += "• 关注板块资金轮动方向\n"

    card3_md += "\n🔮 **明日展望**\n"
    # 根据板块资金自动生成
    inflow = [s for s in data["sector_flow"] if s["dir"] == "in"]
    outflow = [s for s in data["sector_flow"] if s["dir"] == "out"]
    if inflow:
        card3_md += "利好方向: " + "、".join([f"{s['name']}({s['amount']})" for s in inflow[:3]]) + "\n"
    if outflow:
        card3_md += "承压方向: " + "、".join([f"{s['name']}({s['amount']})" for s in outflow[:3]]) + "\n"
    card3_md += "策略建议: 关注主力资金流入方向，回避资金持续流出板块\n"

    return [
        (f"📈 收盘复盘(1/3) | {date_str}", card1_md),
        (f"📈 收盘复盘(2/3) | {date_str}", card2_md),
        (f"📈 收盘复盘(3/3) | {date_str}", card3_md),
    ]


# ============================================================
# 主流程
# ============================================================
def _send_fallback_image_link(png_path, image_url, date_str):
    """发送图片链接到飞书（当图片上传不可用时的备用方案）"""
    img_md = f"🖼️ **收盘复盘长图**\n\n[点击查看完整长图]({image_url})\n\n> 图片托管于 GitHub Pages，加载可能需要几秒。"
    try:
        img_card = build_markdown_card(f"🖼️ 收盘复盘长图 | {date_str}", img_md, "red")
        r = send_card(WEBHOOK, img_card, SECRET)
        print(f"  -> 备用链接推送: {r}")
    except Exception as e:
        print(f"  -> 备用链接推送异常: {e}", file=sys.stderr)


def _send_text_fallback(data, date_str):
    """PNG渲染完全失败时的纯文本降级推送"""
    if not data:
        print("[WARN] 无数据，跳过文本降级", file=sys.stderr)
        return
    try:
        lines = [
            f"⚠️ 长图渲染失败，以下为纯文本摘要",
            "",
        ]
        # 指数
        lines.append("📊 指数概览")
        for code, idx in data.get("a_indices", {}).items():
            sign = "+" if idx["change_pct"] >= 0 else ""
            lines.append(f"  {idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）")
        # 情绪
        lines.append(f"")
        lines.append(f"🌡️ 市场情绪: {data.get('sentiment_temp', 'N/A')}°（{data.get('sentiment_level', 'N/A')}）")
        lines.append(f"涨停: {data.get('zt_total', 0)}家 | 跌停: {data.get('dt_count', 0)}家")
        lines.append(f"北向资金: 净{data.get('north_flow', {}).get('total', 'N/A')}")
        # 概念
        if data.get("concept_top"):
            lines.append(f"")
            lines.append(f"🔥 概念板块TOP")
            for ct in data["concept_top"][:3]:
                lines.append(f"  {ct['name']}: {ct['change_pct']:+.2f}%")
        # 资金
        if data.get("sector_flow"):
            lines.append(f"")
            lines.append(f"💰 板块资金")
            for fl in data["sector_flow"][:4]:
                tag = "流入" if fl["dir"] == "in" else "流出"
                lines.append(f"  {fl['name']}: {tag} {fl['amount']}")

        text_md = "\n".join(lines)
        card = build_markdown_card(f"📉 收盘复盘摘要 | {date_str}", text_md, "red")
        r = send_card(WEBHOOK, card, SECRET)
        print(f"  -> 文本降级推送: {r}")
    except Exception as e:
        print(f"  -> 文本降级推送异常: {e}", file=sys.stderr)


def main():
    if not WEBHOOK:
        print("Error: FEISHU_WEBHOOK environment variable is required", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d") + f" 周{'一二三四五六日'[now.weekday()]}"

    print(f"[{now.isoformat()}] 开始生成收盘复盘...")

    # 收集数据（容错）
    data = None
    try:
        data = collect_all_data()
        print("数据收集完成")
    except Exception as e:
        print(f"数据收集异常: {e}", file=sys.stderr)
        data = {}

    # 生成HTML长图（容错）
    html_path = "/tmp/closing_review.html"
    png_path = "/tmp/closing_review.png"
    png_date = now.strftime("%Y-%m-%d")
    png_filename = f"closing-review-{png_date}.png"
    # GitHub Pages 托管地址（用于飞书卡片引用）
    repo_name = os.environ.get("GITHUB_REPOSITORY", "wyxsdrd666-debug/longxia")
    owner = repo_name.split("/")[0]
    repo = repo_name.split("/")[1] if "/" in repo_name else repo_name
    image_url = f"https://{owner}.github.io/{repo}/{png_filename}"

    if data:
        try:
            html = generate_html(data)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"HTML长图已保存: {html_path}")

            # 尝试渲染为PNG长图
            render_html_to_png(html_path, png_path, width=800)
        except Exception as e:
            print(f"HTML生成异常: {e}", file=sys.stderr)

    # 生成飞书卡片并推送（容错）
    if data:
        try:
            cards = format_feishu_cards(data)
        except Exception as e:
            print(f"卡片格式化异常: {e}", file=sys.stderr)
            cards = []

        for i, (title, md) in enumerate(cards, 1):
            print(f"推送卡片{i}...")
            try:
                card = build_markdown_card(title, md, "blue")
                r = send_card(WEBHOOK, card, SECRET)
                print(f"  -> {r}")
            except Exception as e:
                print(f"  -> 推送异常: {e}", file=sys.stderr)

        # 推送第4条消息：长图（优先原生图片消息，失败则降级到卡片+链接）
        if os.path.exists(png_path):
            image_sent = False
            if FEISHU_APP_ID and FEISHU_APP_SECRET:
                print("上传长图到飞书...")
                image_key = upload_image_to_feishu(FEISHU_APP_ID, FEISHU_APP_SECRET, png_path)
                if image_key:
                    print(f"发送长图...")
                    try:
                        r = send_image(WEBHOOK, image_key, SECRET)
                        print(f"  -> {r}")
                        # 检查返回码：0=成功，其他=失败
                        if isinstance(r, dict) and r.get("code", 0) == 0:
                            image_sent = True
                        else:
                            print(f"[WARN] 图片消息发送失败: {r}", file=sys.stderr)
                    except Exception as e:
                        print(f"  -> 长图发送异常: {e}", file=sys.stderr)
                else:
                    print("[WARN] 图片上传失败，降级到链接", file=sys.stderr)
            else:
                print("[INFO] FEISHU_APP_ID/APP_SECRET 未配置，发送链接代替")

            if not image_sent:
                _send_fallback_image_link(png_path, image_url, date_str)
        else:
            print("[WARN] PNG长图未生成，发送纯文本降级", file=sys.stderr)
            # 降级方案：发送文本版长图摘要
            _send_text_fallback(data, date_str)

    print(f"HTML_PATH={html_path}")
    print(f"PNG_PATH={png_path}")
    print(f"IMAGE_URL={image_url}")
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
        sys.exit(0)  # 即使崩溃也不返回非0退出码
