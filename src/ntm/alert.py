"""预警推送（企业微信 / 邮件 / 站内），带去重降噪与频率上限。

凭据只从环境变量读取，不落盘、不打印。推送文案必须携带
「推断非实锤」与免责声明——对应《每日监控清单》D11。
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

from . import config, db
from .signals import POLICY_BOTTOM_PROBABLE


def _message(signal: dict) -> str:
    c = signal.get("conditions", {})
    return (
        f"【国家队资金监控 · 托底信号】{signal.get('date')}\n"
        f"状态：{signal.get('state_label')}\n"
        f"置信度：{signal.get('confidence_label')}（推断，非实锤；无法确权到具体主体）\n"
        f"三重共振：\n"
        f"  ① 市场大跌：{'满足' if c.get('market_drop', {}).get('met') else '未满足'}\n"
        f"  ② 多只宽基份额同步跳升：{'满足' if c.get('etf_sync', {}).get('met') else '未满足'}\n"
        f"  ③ 官方增持公告：{'满足' if c.get('official', {}).get('met') else '未满足'}\n"
        f"说明：{signal.get('summary', '')}\n"
        f"提示：政策底≠市场底，系统不提供个股买卖建议，不构成投资建议。"
    )


def dispatch(signal: dict, force: bool = False) -> list[dict]:
    """返回各渠道投递结果。未配置渠道标记为 skipped（不视为失败）。"""
    day = signal["date"]
    state = signal["state"]
    results: list[dict] = []

    # 只对政策底信号推送，避免噪声轰炸
    if state != POLICY_BOTTOM_PROBABLE and not force:
        return [{"channel": "none", "status": "skipped", "reason": "非政策底信号，不推送"}]

    if not force and db.count_alerts_on(day) >= config.ALERT_MAX_PER_DAY:
        return [{"channel": "none", "status": "skipped", "reason": "已达每日推送上限"}]

    # 同状态同日去重
    if not force:
        with db.connect() as conn:
            last = conn.execute(
                "SELECT state FROM alerts WHERE date=? ORDER BY id DESC LIMIT 1", (day,)
            ).fetchone()
        if last and last["state"] == state:
            return [{"channel": "none", "status": "skipped", "reason": "同状态同日已推送"}]

    text = _message(signal)

    # 站内（始终记录）
    db.insert_alert(day, state, "inbox", "sent")
    results.append({"channel": "inbox", "status": "sent"})

    # 企业微信。requests 只在确实要推送时导入，避免演示模式被可选依赖拖垮。
    if config.WECHAT_WEBHOOK:
        status = "failed"
        reason = None
        try:
            import requests
        except ImportError:
            reason = "未安装 requests，无法发送企业微信"
        else:
            try:
                r = requests.post(
                    config.WECHAT_WEBHOOK,
                    json={"msgtype": "text", "text": {"content": text}},
                    timeout=8,
                )
                status = "sent" if r.ok else "failed"
            except Exception:  # noqa: BLE001
                status = "failed"
        db.insert_alert(day, state, "wecom", status)
        item: dict[str, Any] = {"channel": "wecom", "status": status}
        if reason:
            item["reason"] = reason
        results.append(item)
    else:
        results.append({"channel": "wecom", "status": "skipped", "reason": "未配置 webhook"})

    # 邮件
    if config.SMTP_HOST and config.SMTP_TO:
        try:
            msg = EmailMessage()
            msg.set_content(text)
            msg["Subject"] = f"[NTM] 托底信号 {day}（推断非实锤）"
            msg["From"] = config.SMTP_TO
            msg["To"] = config.SMTP_TO
            with smtplib.SMTP(config.SMTP_HOST, timeout=8) as s:
                s.send_message(msg)
            status = "sent"
        except Exception:  # noqa: BLE001
            status = "failed"
        db.insert_alert(day, state, "email", status)
        results.append({"channel": "email", "status": status})
    else:
        results.append({"channel": "email", "status": "skipped", "reason": "未配置 SMTP"})

    return results


def alert_status() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT channel,status,COUNT(*) n FROM alerts GROUP BY channel,status"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}
