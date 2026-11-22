"""Weixin (personal WeChat via iLink Bot API) env -> gateway config bridge (``PlatformEntry.env_overrides``)."""

from gateway.config import Platform
from gateway.platform_env import EnvCred, enable_from_env, env_home_channel, strip_slash


def _enable_registered_accounts(config):
    from hermes_constants import get_hermes_home
    from .adapter import load_indexed_weixin_account_ids

    if load_indexed_weixin_account_ids(str(get_hermes_home())):
        enable_from_env(config, Platform.WEIXIN)
        env_home_channel(config, Platform.WEIXIN, "WEIXIN_HOME_CHANNEL", strip=True)

ENV_ENABLE_CREDENTIALS = ("WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID")
ENV_OVERRIDES = (
    EnvCred(
        Platform.WEIXIN, (("WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID"),), token="WEIXIN_TOKEN",
        optional=(("account_id", "WEIXIN_ACCOUNT_ID"),),
        optional_stripped=(
            ("base_url", "WEIXIN_BASE_URL", strip_slash), ("cdn_base_url", "WEIXIN_CDN_BASE_URL", strip_slash),
            ("dm_policy", "WEIXIN_DM_POLICY", str.lower), ("group_policy", "WEIXIN_GROUP_POLICY", str.lower),
            ("allow_from", "WEIXIN_ALLOWED_USERS"), ("group_allow_from", "WEIXIN_GROUP_ALLOWED_USERS"),
            ("split_multiline_messages", "WEIXIN_SPLIT_MULTILINE_MESSAGES"),
        ),
        home="WEIXIN_HOME_CHANNEL", home_strip=True,
    ),
    _enable_registered_accounts,
)

# Authorization env names (``PlatformEntry`` fields). Core reads them from this file when the
# live entry lacks them: a user plugin overriding this platform, safe mode, the plugin disabled.
AUTHZ = {
    "allowed_users_env": "WEIXIN_ALLOWED_USERS",
    "allow_all_env": "WEIXIN_ALLOW_ALL_USERS",
    "own_policy_env": ("WEIXIN_DM_POLICY", "WEIXIN_GROUP_POLICY"),
    "allowlist_suppresses_pairing": True,
}
