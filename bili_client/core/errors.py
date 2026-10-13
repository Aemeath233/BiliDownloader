from __future__ import annotations

import re


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def humanize_error(message: str) -> str:
    text = str(message or "").strip()
    lowered = text.lower()

    if not text:
        return "操作失败，但没有拿到具体原因。可以稍后重试一次。"

    if "所选画质" in text and "实际返回" in text:
        return text

    if _contains_any(
        lowered, ("certificate_verify_failed", "certificate verify failed", "unable to get local issuer certificate")
    ):
        return "HTTPS 证书校验失败，请检查系统时间和受信任证书；如使用代理或抓包软件，请确认其证书已正确安装。"

    if "预览" in text:
        return "当前账号只能获取预览片段，完整内容可能需要会员、购片或版权权限。"

    if _contains_any(lowered, ("sessdata", "cookie", "login required")) or _contains_any(
        text, ("未登录", "登录", "-101", "账号")
    ):
        return "登录状态可能已经失效，请在设置页扫码登录后重试。"

    if _contains_any(lowered, ("403", "401", "unauthorized", "forbidden")) or _contains_any(
        text, ("权限", "会员", "大会员", "付费", "版权", "区域限制")
    ):
        return "当前账号可能没有这个视频或画质的访问权限，可以换低一点的画质，或确认会员/版权权限后重试。"

    if _contains_any(lowered, ("429", "412", "captcha")) or _contains_any(
        text, ("风控", "验证码", "频繁", "请求过于频繁", "访问受限")
    ):
        return "请求被 B 站限制了，建议稍等一会儿再试，或重新扫码登录后重试。"

    if _contains_any(lowered, ("timeout", "timed out", "readtimeout", "connecttimeout")) or _contains_any(
        text, ("超时", "连接失败", "网络请求失败")
    ):
        return "网络连接超时了，请检查网络后重试。"

    if _contains_any(lowered, ("500", "502", "503", "504")):
        return "B 站服务或网络节点暂时不稳定，可以稍后再试。"

    if _contains_any(lowered, ("ffmpeg", "invalid data", "merge")) or _contains_any(text, ("合并", "音视频")):
        return "音视频合并失败，请检查 FFmpeg 是否可用，或重新下载该任务。"

    if re.search(r"https?://", text):
        return "媒体流下载失败，可能是链接过期、网络波动或当前账号权限不足。可以重试一次。"

    return text
