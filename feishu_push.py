#!/usr/bin/env python3
"""飞书群机器人消息推送 - 支持签名校验(HMAC-SHA256)"""

import json
import time
import base64
import hmac
import hashlib
import urllib.request
import sys
import os


def gen_sign(timestamp, secret):
    """生成HMAC-SHA256签名"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_card(webhook_url, card, secret=None):
    """
    发送飞书卡片消息
    card: dict 或 str，飞书卡片 JSON
    """
    timestamp = str(int(time.time()))
    msg_body = {
        "timestamp": timestamp,
        "msg_type": "interactive",
        "card": card if isinstance(card, dict) else json.loads(card)
    }
    if secret:
        msg_body["sign"] = gen_sign(timestamp, secret)

    data = json.dumps(msg_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def send_text(webhook_url, text, secret=None):
    """发送纯文本消息"""
    timestamp = str(int(time.time()))
    msg_body = {
        "timestamp": timestamp,
        "msg_type": "text",
        "content": {"text": text}
    }
    if secret:
        msg_body["sign"] = gen_sign(timestamp, secret)

    data = json.dumps(msg_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def build_markdown_card(title, md_content, color="blue"):
    """构建飞书 Markdown 卡片消息"""
    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color
        },
        "elements": [
            {
                "tag": "markdown",
                "content": md_content
            }
        ]
    }


if __name__ == "__main__":
    # 配置 - 必须通过环境变量设置，不再硬编码默认值
    WEBHOOK = os.environ.get("FEISHU_WEBHOOK")
    SECRET = os.environ.get("FEISHU_SECRET")
    if not WEBHOOK:
        print("Error: FEISHU_WEBHOOK environment variable is required", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python feishu_push.py <command> [args...]")
        print("Commands:")
        print("  card <title> <color> <json_or_md>  - 发送卡片消息")
        print("  text <message>                     - 发送文本消息")
        print("  json <json_file>                   - 从文件读取JSON发送")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "text":
        text = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
        r = send_text(WEBHOOK, text, SECRET)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("code") == 0 else 1)

    elif cmd == "card":
        title = sys.argv[2] if len(sys.argv) > 2 else "消息"
        color = sys.argv[3] if len(sys.argv) > 3 else "blue"
        content = sys.argv[4] if len(sys.argv) > 4 else sys.stdin.read()
        card = build_markdown_card(title, content, color)
        r = send_card(WEBHOOK, card, SECRET)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("code") == 0 else 1)

    elif cmd == "json":
        path = sys.argv[2]
        with open(path, "r", encoding="utf-8") as f:
            card = json.load(f)
        r = send_card(WEBHOOK, card, SECRET)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("code") == 0 else 1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
