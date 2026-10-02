"""运营后台鉴权。

当前为最小可用实现：以共享令牌校验运营写操作，缺省拒绝。
正式多租户 RBAC/OIDC 见 ARCH.md §7，此处为迁移预留稳定接口。
"""

from hmac import compare_digest

from fastapi import Header, HTTPException

from app.core.config import get_settings

ADMIN_TOKEN_HEADER = "X-Admin-Token"


async def require_admin(
    admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> str:
    """校验运营后台令牌。未配置令牌时一律拒绝。"""
    expected = get_settings().admin_api_token
    if not expected:
        # 未配置凭证视为未启用后台写能力，默认拒绝而非放行。
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED"})
    if not admin_token or not compare_digest(admin_token, expected):
        raise HTTPException(status_code=401, detail={"code": "ADMIN_AUTH_INVALID"})
    return "admin"
