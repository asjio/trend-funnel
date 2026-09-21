# -*- coding: utf-8 -*-
"""nextday_v2 通知模块 (Server酱 -> 个人微信)
用法: python -c "from nextday_v2.notifier import send; send('标题', '内容')"

获取 SendKey:
  1. 打开 https://sct.ftqq.com 微信扫码登录
  2. 复制 SendKey (格式: SCT123456...)
  3. 设环境变量 NOTIFY_SENDKEY 或直接改下方默认值

Server酱原理: 通过微信服务号推送模板消息到你个人微信
"""
import json
import os
import urllib.parse
import urllib.request

# 优先读环境变量, 没有则改这里
NOTIFY_SENDKEY = os.environ.get("NOTIFY_SENDKEY", "SCTxxxxxxxxxxxxxxxxxxxxxxxxxx")


def send(title: str, body: str):
    """发送到个人微信 (通过 Server酱)"""
    if "xxxxxxxx" in NOTIFY_SENDKEY:
        print("[通知] SendKey 未配置, 跳过通知", flush=True)
        return

    url = f"https://sctapi.ftqq.com/{NOTIFY_SENDKEY}.send"
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
        if result.get("code") == 0:
            print("[通知] 已发送到微信", flush=True)
        else:
            print(f"[通知] 失败: {result.get('message', result)}", flush=True)
    except Exception as e:
        print(f"[通知] 发送异常: {e}", flush=True)