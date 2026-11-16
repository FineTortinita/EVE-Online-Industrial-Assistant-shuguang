"""曙光 SSO 实测脚本（手动运行，不进 CI、不写任何库）。

走一遍与应用完全相同的授权链路：PKCE → 浏览器登录网易 → 粘贴回跳地址 → 换令牌 → 刷新一次 →
调一次技能接口。只打印**脱敏**结果：令牌只显示长度、claims 只显示键名与非敏感字段，
可以直接把输出贴给开发者排查。

用法（仓库根）：
    .venv/Scripts/python.exe -m scripts.sso_live_check
"""

from __future__ import annotations

import asyncio
import json
import secrets
import webbrowser

import aiohttp

from ui_qml.workers import esi_skill_worker as esw

#: 只打印这些 claim 的值；其余只列键名（sub/name 用于确认角色身份格式，不含令牌本体）
_SAFE_CLAIMS = ("iss", "aud", "azp", "sub", "name", "scp", "exp", "tenant", "region", "kid")


def _redact(payload: dict) -> dict:
    return {k: (f"<{len(str(v))} chars>" if "token" in k else v) for k, v in payload.items()}


async def _post(session: aiohttp.ClientSession, data: dict) -> tuple[int, dict]:
    async with session.post(esw.TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        text = await resp.text()
        try:
            body = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            body = {"raw": text[:300]}
        return resp.status, body


async def main() -> int:
    verifier, challenge = esw._pkce_pair()
    state = secrets.token_urlsafe(16)

    async with aiohttp.ClientSession(headers={"Accept": "application/json"}) as session:
        device_id = await esw._fetch_device_id(session)
        print(f"0) 网易设备 ID 申请成功（{len(device_id)} 字符）")
        url = esw._authorize_url(challenge, state, device_id=device_id)
        print("1) 浏览器将打开网易授权页（也可手动复制下面的地址）：\n   " + url)
        webbrowser.open(url)
        pasted = input("2) 登录并选好角色后，把空白页地址栏的完整网址粘贴到这里，回车：\n> ")
        code = esw.parse_redirect(pasted, state)
        print("   回跳地址校验通过（官方回调页 + state 一致）")

        base = {"client_id": esw.CLIENT_ID, "redirect_uri": esw.CALLBACK_URL}
        status, tok = await _post(
            session, {**base, "grant_type": "authorization_code", "code": code, "code_verifier": verifier}
        )
        print(f"3) 换令牌：HTTP {status} {_redact(tok)}")
        if status != 200:
            return 1
        claims = esw._jwt_claims(str(tok.get("access_token") or ""))
        print("   claims 键：", sorted(claims))
        print("   claims 值：", {k: claims[k] for k in _SAFE_CLAIMS if k in claims})
        try:
            esw._check_claims(claims)
            char_id, char_name = esw._character_from_claims(claims, None, None)
            print(f"   应用侧校验通过：角色 {char_name}（{char_id}）")
        except RuntimeError as e:
            print(f"   ⚠ 应用侧校验未通过：{e}  ← 需要按上面的 claims 调整校验规则")
            return 1

        status, ref = await _post(
            session, {**base, "grant_type": "refresh_token", "refresh_token": str(tok.get("refresh_token") or "")}
        )
        rotated = ref.get("refresh_token") not in (None, tok.get("refresh_token"))
        print(f"4) 刷新：HTTP {status} {_redact(ref)}（refresh_token 已轮换：{rotated}）")
        access = str(ref.get("access_token") or tok.get("access_token"))

        async with session.get(
            esw.esi_url(f"/characters/{char_id}/skills/"), headers={"Authorization": f"Bearer {access}"}
        ) as resp:
            body = await resp.json(content_type=None)
            n = len(body.get("skills", [])) if isinstance(body, dict) else 0
            print(f"5) 技能接口：HTTP {resp.status}，技能条数 {n}，总技能点 {body.get('total_sp') if n else body}")
            return 0 if resp.status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
