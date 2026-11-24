"""Tests for Weixin reply extraction and local quote resolution."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.weixin import adapter as weixin
from plugins.platforms.weixin import quote_store
from plugins.platforms.weixin.quote_store import WeixinQuote, WeixinQuoteStore, resolve_partial_quote


@pytest.mark.parametrize(
    ("ref_msg", "expected"),
    [
        ({"title": "Original title"}, "[引用: Original title]\nFollow up"),
        ({"title": "Title", "message_item": {"type": weixin.ITEM_TEXT, "text_item": {"text": "Original text"}}},
         "[引用: Title | Original text]\nFollow up"),
        ({"title": "Photo", "message_item": {"type": weixin.ITEM_IMAGE}}, "[引用媒体: Photo]\nFollow up"),
        ({"message_item": {"type": weixin.ITEM_IMAGE}}, "[引用媒体]\nFollow up"),
    ],
)
def test_extract_text_preserves_legacy_inline_quotes(ref_msg, expected):
    item = {"type": weixin.ITEM_TEXT, "text_item": {"text": "Follow up"}, "ref_msg": ref_msg}
    assert weixin._extract_text([item]) == expected


def _md5(text):
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def test_partial_quote_matches_global_and_relative_tencent_indices():
    text = "start 开头 end start 正文 end tail end"
    partial = {"start": "start", "end": "end", "startindex": 1, "endindex": 1}
    assert resolve_partial_quote(text, partial) == ("start 正文 end", False)
    relative = "start 正文 end tail end"
    assert resolve_partial_quote(text, {**partial, "quotemd5": _md5(relative).upper()}) == (relative, False)
    assert resolve_partial_quote(text, {**partial, "quotemd5": _md5("different quote")}) == (None, True)
    assert resolve_partial_quote(text, {**partial, "endindex": 0}) == ("start 正文 end", False)


@pytest.mark.parametrize(
    ("text", "overrides", "missing"),
    [
        ("", {}, ()),
        ("start end", {"start": ""}, ()),
        ("start end", {"end": []}, ()),
        ("start end", {"startindex": -1}, ()),
        ("start end", {"startindex": True}, ()),
        ("start end", {"endindex": "0"}, ()),
        ("start end", {"endindex": 10**20}, ()),
        ("start end", {}, ("endindex",)),
    ],
)
def test_partial_quote_rejects_invalid_input(text, overrides, missing):
    partial = {"start": "start", "end": "end", "startindex": 0, "endindex": 0, **overrides}
    for key in missing:
        del partial[key]
    assert resolve_partial_quote(text, partial) == (None, True)


def test_quote_store_isolates_keys_and_preserves_uint64_and_media(tmp_path):
    store = WeixinQuoteStore(tmp_path)
    message_id = "18446744073709551615"
    media = [{"path": str(tmp_path / "图.jpg"), "mime": "image/jpeg", "name": "图.jpg"}]
    assert store.put("bot-one", "chat-one", message_id, direction="outbound", body="你好", media=media)
    assert store.put("bot-one", "chat-two", message_id, direction="inbound", body="b" * 70_000)
    assert store.put("bot-two", "chat-one", message_id, direction="inbound", body="Other account")
    reopened = WeixinQuoteStore(tmp_path)
    assert reopened.find("bot-one", "chat-one", message_id) == WeixinQuote("outbound", "你好", media)
    assert reopened.find("bot-one", "chat-two", message_id) == WeixinQuote("inbound", "b" * 65_536, [])
    assert reopened.find("bot-two", "chat-one", message_id) == WeixinQuote("inbound", "Other account", [])
    assert reopened.find("bot-two", "chat-two", message_id) is None
    assert reopened.find("bot-one", "chat-one", "18446744073709551614") is None


def test_quote_store_prunes_age_and_caps_each_account(tmp_path, monkeypatch):
    store = WeixinQuoteStore(tmp_path, retention_days=1, max_messages_per_account=2)
    start = 1_800_000_000
    monkeypatch.setattr(quote_store.time, "time", lambda: start)
    assert store.put("bot-one", "chat", "old", direction="inbound", body="expired")
    monkeypatch.setattr(quote_store.time, "time", lambda: start + 86_401)
    assert store.find("bot-one", "chat", "old") is None
    assert store.put("bot-two", "chat", "one", direction="inbound", body="separate account")
    for message_id in ("one", "two", "three"):
        assert store.put("bot-one", "chat", message_id, direction="inbound", body=message_id)
    assert store.find("bot-one", "chat", "one") is None
    assert store.find("bot-one", "chat", "two") == WeixinQuote("inbound", "two", [])
    assert store.find("bot-one", "chat", "three") == WeixinQuote("inbound", "three", [])
    assert store.find("bot-two", "chat", "one") == WeixinQuote("inbound", "separate account", [])
    monkeypatch.setattr(quote_store.time, "time", lambda: start)
    assert store.find("bot-one", "chat", "old") is None


def test_quote_store_handles_concurrent_writes(tmp_path):
    store = WeixinQuoteStore(tmp_path)

    def write(message_id):
        return store.put("bot", "chat", message_id, direction="outbound", body=message_id)

    message_ids = [str(index) for index in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(write, message_ids))
    assert [store.find("bot", "chat", message_id).body for message_id in message_ids] == message_ids


def test_quote_store_fails_open_without_logging_content(tmp_path, caplog):
    (tmp_path / "weixin" / "quote_cache.db").mkdir(parents=True)
    store = WeixinQuoteStore(tmp_path)
    assert not store.put("bot", "chat", "id", direction="inbound", body="private fixture body")
    assert store.find("bot", "chat", "id") is None
    assert "Weixin quote cache write failed" in caplog.text
    assert "Weixin quote cache read failed" in caplog.text
    assert "private fixture body" not in caplog.text


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(weixin, "get_hermes_home", lambda: tmp_path)
    adapter = weixin.WeixinAdapter(PlatformConfig(enabled=True, token="fixture-token", extra={"account_id": "bot"}))
    adapter._send_session = adapter._poll_session = object()
    adapter._typing_cache.set("peer", "fixture-ticket")
    adapter.handle_message = AsyncMock()
    return adapter


def _inbound(message_id, text, *, ref_msg=None, media=()):
    text_item = {"type": weixin.ITEM_TEXT, "text_item": {"text": text}}
    if ref_msg is not None:
        text_item["ref_msg"] = ref_msg
    return {"from_user_id": "peer", "message_id": message_id, "item_list": [text_item, *media]}


@pytest.mark.asyncio
async def test_outbound_text_exposes_server_ids_for_all_chunks(adapter, monkeypatch):
    adapter._send_chunk_delay_seconds = 0
    monkeypatch.setattr(adapter, "_split_text", lambda _text: ["First", "Second", "Third"])
    send = AsyncMock(side_effect=[{"message_id": "18446744073709551615"}, {"message_id": 123}, {}])
    monkeypatch.setattr(weixin, "_send_message", send)
    result = await adapter.send("peer", "Message")
    fallback_id = send.await_args.kwargs["client_id"]
    assert result.success
    assert result.message_id == "18446744073709551615"
    assert result.continuation_message_ids == ("123", fallback_id)
    assert adapter._quote_store.find("bot", "peer", result.message_id) == WeixinQuote("outbound", "First", [])
    assert adapter._quote_store.find("bot", "peer", "123") == WeixinQuote("outbound", "Second", [])
    assert adapter._quote_store.find("bot", "peer", fallback_id) is None


@pytest.mark.asyncio
async def test_outbound_file_caches_caption_and_media_under_their_server_ids(adapter, tmp_path, monkeypatch):
    image = tmp_path / "photo.png"
    image.write_bytes(b"fixture image")
    monkeypatch.setattr(weixin, "_get_upload_url", AsyncMock(return_value={"upload_full_url": "https://example.test/upload"}))
    monkeypatch.setattr(weixin, "_upload_ciphertext", AsyncMock(return_value="fixture-query"))
    monkeypatch.setattr(weixin, "_send_items", AsyncMock(side_effect=[{"message_id": "caption-id"}, {"message_id": "media-id"}]))
    result = await adapter.send_image_file("peer", str(image), caption="**A photo**")
    assert result.success and result.message_id == "media-id"
    assert adapter._quote_store.find("bot", "peer", "caption-id") == WeixinQuote("outbound", adapter.format_message("**A photo**"), [])
    assert adapter._quote_store.find("bot", "peer", "media-id") == WeixinQuote(
        "outbound", "[图片]", [{"path": str(image), "mime": "image/png", "name": "photo.png"}],
    )


@pytest.mark.asyncio
async def test_reply_to_previously_sent_text_builds_one_gateway_pointer(adapter, monkeypatch):
    from gateway.run import GatewayRunner

    ref_id = "18446744073709551615"
    monkeypatch.setattr(weixin, "_send_message", AsyncMock(return_value={"message_id": ref_id}))
    assert (await adapter.send("peer", "Original bot message")).message_id == ref_id
    await adapter._process_message(_inbound("reply", "展开讲讲", ref_msg={"svr_id": ref_id}))
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "展开讲讲"
    assert event.reply_to_message_id == ref_id
    assert event.reply_to_text == "Original bot message"
    assert event.reply_to_is_own_message is True
    assert GatewayRunner._prepend_inbound_reply_context(event, event.source, event.text) == (
        '[Replying to your previous message: "Original bot message"]\n\n展开讲讲'
    )


@pytest.mark.asyncio
async def test_partial_reply_uses_substring_or_full_cached_body(adapter):
    original = "Before: 选中的一段. After."
    adapter._quote_store.put("bot", "peer", "original", direction="outbound", body=original)
    partial = {"start": "选中", "end": "一段", "startindex": 0, "endindex": 0, "quotemd5": _md5("选中的一段")}
    await adapter._process_message(_inbound("reply-one", "展开讲讲", ref_msg={"svr_id": "original", "partial_text": partial}))
    await adapter._process_message(_inbound(
        "reply-two", "展开讲讲", ref_msg={"svr_id": "original", "partial_text": {**partial, "quotemd5": "invalid"}},
    ))
    assert [call.args[0].reply_to_text for call in adapter.handle_message.await_args_list] == ["选中的一段", original]


@pytest.mark.asyncio
async def test_reply_to_inbound_media_uses_item_id_and_excludes_referenced_media_from_new_record(adapter, tmp_path, monkeypatch):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"fixture image")
    download = AsyncMock(return_value=(str(image), "image/jpeg"))
    monkeypatch.setattr(adapter, "_download_media", download)
    await adapter._process_message(_inbound("", "", media=[{"type": weixin.ITEM_IMAGE, "msg_id": "original"}]))
    reply = _inbound("reply", "这张图")
    reply["ref_msg"] = {"svr_id": "original"}
    await adapter._process_message(reply)
    event = adapter.handle_message.await_args.args[0]
    assert event.reply_to_message_id == "original"
    assert event.reply_to_text == "[图片]"
    assert event.reply_to_is_own_message is False
    assert event.media_urls == [str(image)] and event.media_types == ["image/jpeg"]
    assert adapter._quote_store.find("bot", "peer", "reply") == WeixinQuote("inbound", "这张图", [])
    image.unlink()
    await adapter._process_message(_inbound("reply-later", "What was here?", ref_msg={"svr_id": "original"}))
    event = adapter.handle_message.await_args.args[0]
    assert event.reply_to_text == "[图片]"
    assert event.media_urls == []
    download.assert_awaited_once()


@pytest.mark.asyncio
async def test_unseen_quotes_with_distinct_ids_are_delivered_separately(adapter, caplog):
    caplog.set_level(logging.INFO, logger=weixin.__name__)
    for index, ref_id in enumerate(("uncached-one", "uncached-two", "uncached-two")):
        await adapter._process_message(_inbound(str(index), "展开讲讲", ref_msg={"svr_id": ref_id}))
    events = [call.args[0] for call in adapter.handle_message.await_args_list]
    assert [event.reply_to_message_id for event in events] == ["uncached-one", "uncached-two"]
    assert all(event.reply_to_text == "[引用消息内容未缓存]" for event in events)
    assert all(event.text == "展开讲讲" and not event.reply_to_is_own_message for event in events)
    assert "ref_id=True inline=False cache_hit=False partial=False" in caplog.text
    assert "展开讲讲" not in caplog.text


@pytest.mark.asyncio
async def test_legacy_inline_title_is_delivered_without_a_reply_id(adapter):
    await adapter._process_message(_inbound("reply", "Follow up", ref_msg={"title": "Original title"}))
    event = next(iter(adapter._pending_text_batches.values()))
    await adapter._flush_text_batch_now(adapter._text_batch_key(event))
    adapter.handle_message.assert_awaited_once()
    assert event.text == "[引用: Original title]\nFollow up"
    assert event.reply_to_message_id is None and event.reply_to_text is None


@pytest.mark.asyncio
async def test_inline_quote_prefers_title_and_recovers_failed_media_from_cache(adapter, tmp_path, monkeypatch):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"fixture image")
    adapter._quote_store.put("bot", "peer", "original", direction="outbound", body="[图片]", media=[
        {"path": str(image), "mime": "image/jpeg", "name": "photo.jpg"},
    ])
    monkeypatch.setattr(adapter, "_download_media", AsyncMock(return_value=(None, "image/jpeg")))
    await adapter._process_message(_inbound("reply", "Follow up", ref_msg={
        "svr_id": "original", "title": "Original image", "message_item": {"type": weixin.ITEM_IMAGE},
    }))
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "Follow up"
    assert event.reply_to_message_id == "original"
    assert event.reply_to_text == "Original image"
    assert event.reply_to_is_own_message is True
    assert event.media_urls == [str(image)]
