"""曙光 SSO 实测脚本（手动运行，不进 CI、不写任何库）。

走一遍与应用完全相同的授权链路：PKCE → 浏览器登录网易 → 粘贴回跳地址 → 换令牌 → 刷新一次 →
调一次技能接口。只打印**脱敏**结果：令牌只显示长度、claims 只显示键名与非敏感字段，
可以直接把输出贴给开发者排查。

用法（仓库根）：
    .venv/Scripts/python.exe -m scripts.sso_live_check
    .venv/Scripts/python.exe -m scripts.sso_live_check --industry   # 额外申请资产 / 蓝图 / 工业作业 scope 并逐个调接口
    .venv/Scripts/python.exe -m scripts.sso_live_check --industry=assets,jobs   # 只申请其中几个（assets / blueprints / jobs）

`--industry` 用来验证网易公共客户端是否放行这几个 scope（重构阶段 6 的前置，2026-11-17 加）：
授权页对任意 scope 都先 302 到登录页，**放不放行只有登录后才知道** —— 不放行时授权页报
`invalid_scope`（第 2 步拿不到回跳地址），放行时第 6 步逐个接口打一次、只打印 HTTP 状态与条数。
2026-11-17 用户实测：`esi-characters.read_blueprints.v1` → `invalid_scope`（公共客户端不放行）。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import webbrowser

import aiohttp

from ui_qml.workers import esi_skill_worker as esw

#: `--industry` 额外申请的 scope → 用它验证的接口（{id} = 角色 id）
_INDUSTRY_PROBES = {
    "esi-assets.read_assets.v1": "/characters/{id}/assets/",
    "esi-characters.read_blueprints.v1": "/characters/{id}/blueprints/",
    "esi-industry.read_character_jobs.v1": "/characters/{id}/industry/jobs/",
}
#: `--industry=` 的短名
_PROBE_KEYS = {
    "assets": "esi-assets.read_assets.v1",
    "blueprints": "esi-characters.read_blueprints.v1",
    "jobs": "esi-industry.read_character_jobs.v1",
}


def _selected_probes(argv: list[str]) -> dict[str, str]:
    """命令行 → 要探测的 {scope: 接口}；`--industry` = 全部，`--industry=a,b` = 只要这几个，没有 = 不探测。"""
    for arg in argv:
        if arg == "--industry":
            return dict(_INDUSTRY_PROBES)
        if arg.startswith("--industry="):
            keys = [k.strip() for k in arg.split("=", 1)[1].split(",") if k.strip()]
            unknown = [k for k in keys if k not in _PROBE_KEYS]
            if unknown:
                raise SystemExit(f"未知探测项 {unknown}，可选：{', '.join(_PROBE_KEYS)}")
            return {_PROBE_KEYS[k]: _INDUSTRY_PROBES[_PROBE_KEYS[k]] for k in keys}
    return {}


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
    probes = _selected_probes(sys.argv[1:])
    extra = tuple(probes)
    verifier, challenge = esw._pkce_pair()
    state = secrets.token_urlsafe(16)

    async with aiohttp.ClientSession(headers={"Accept": "application/json"}) as session:
        device_id = await esw._fetch_device_id(session)
        print(f"0) 网易设备 ID 申请成功（{len(device_id)} 字符）")
        url = esw._authorize_url(challenge, state, extra, device_id=device_id)
        if extra:
            print("   额外申请 scope：", " ".join(extra))
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
            ok = resp.status == 200

        for scope, path in probes.items():
            granted = scope in (claims.get("scp") or [])
            async with session.get(
                esw.esi_url(path.format(id=char_id)), headers={"Authorization": f"Bearer {access}"}
            ) as resp:
                body = await resp.json(content_type=None)
                n = len(body) if isinstance(body, list) else "—"
                pages = resp.headers.get("X-Pages", "1")
                print(
                    f"6) {scope}（令牌里{'有' if granted else '没有'}）→ {path}: HTTP {resp.status}，条数 {n}，页数 {pages}"
                )
                ok = ok and resp.status == 200
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
