"""Tests for the delivery routing module."""

from gateway.config import Platform
from gateway.delivery import DeliveryTarget
from gateway.session import SessionSource


class TestParseTargetPlatformChat:
    def test_explicit_telegram_chat(self):
        target = DeliveryTarget.parse("telegram:12345")
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "12345"
        assert target.is_explicit is True

    def test_platform_only_no_chat_id(self):
        target = DeliveryTarget.parse("discord")
        assert target.platform == Platform.DISCORD
        assert target.chat_id is None
        assert target.is_explicit is False

    def test_local_target(self):
        target = DeliveryTarget.parse("local")
        assert target.platform == Platform.LOCAL
        assert target.chat_id is None

    def test_origin_with_source(self):
        origin = SessionSource(platform=Platform.TELEGRAM, chat_id="789", thread_id="42")
        target = DeliveryTarget.parse("origin", origin=origin)
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "789"
        assert target.thread_id == "42"
        assert target.is_origin is True

    def test_origin_without_source(self):
        target = DeliveryTarget.parse("origin")
        assert target.platform == Platform.LOCAL
        assert target.is_origin is True

    def test_unknown_platform(self):
        target = DeliveryTarget.parse("unknown_platform")
        assert target.platform == Platform.LOCAL

    def test_weixin_explicit_account_target(self):
        target = DeliveryTarget.parse("weixin/abc@im.bot:wxid_xxx")
        assert target.platform == Platform.WEIXIN
        assert target.account_id == "abc@im.bot"
        assert target.chat_id == "wxid_xxx"
        assert target.is_explicit is True

    def test_weixin_target_preserves_account_and_chat_id_case(self):
        target = DeliveryTarget.parse("weixin/Bot-A@Im.Bot:WxId_MixedCase")
        assert target.platform == Platform.WEIXIN
        assert target.account_id == "Bot-A@Im.Bot"
        assert target.chat_id == "WxId_MixedCase"


class TestTargetToStringRoundtrip:
    def test_origin_roundtrip(self):
        origin = SessionSource(platform=Platform.TELEGRAM, chat_id="111", thread_id="42")
        target = DeliveryTarget.parse("origin", origin=origin)
        assert target.to_string() == "origin"

    def test_local_roundtrip(self):
        target = DeliveryTarget.parse("local")
        assert target.to_string() == "local"

    def test_platform_only_roundtrip(self):
        target = DeliveryTarget.parse("discord")
        assert target.to_string() == "discord"

    def test_explicit_chat_roundtrip(self):
        target = DeliveryTarget.parse("telegram:999")
        s = target.to_string()
        assert s == "telegram:999"

        reparsed = DeliveryTarget.parse(s)
        assert reparsed.platform == Platform.TELEGRAM
        assert reparsed.chat_id == "999"

    def test_weixin_account_roundtrip(self):
        target = DeliveryTarget.parse("weixin/abc@im.bot:wxid_xxx")
        assert target.to_string() == "weixin/abc@im.bot:wxid_xxx"

        reparsed = DeliveryTarget.parse(target.to_string())
        assert reparsed.platform == Platform.WEIXIN
        assert reparsed.account_id == "abc@im.bot"
        assert reparsed.chat_id == "wxid_xxx"

