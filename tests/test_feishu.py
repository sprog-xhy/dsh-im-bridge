"""Tests for the Feishu channel (crypto + event mapping).

The AES decrypt and event-parsing paths can be unit-tested without a real
Feishu app; only the network long-connection needs live credentials.
"""

import base64
import hashlib
import json

import pytest

from dsh_im_bridge.channels import feishu
from dsh_im_bridge.channels.feishu import FeishuChannel, _aes_decrypt


def _feishu_encrypt(encrypt_key: str, plaintext: str) -> str:
    """Encrypt the way Feishu does: AES-256-CBC, key=sha256(encrypt_key),
    IV = 16 raw bytes prepended as characters, then base64(ciphertext)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = b"0123456789abcdef"
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    return iv.decode("latin-1") + base64.b64encode(enc).decode("ascii")


def test_aes_decrypt_roundtrip():
    key = "my-encrypt-key"
    plain = '{"type":"url_verification","challenge":"abc123"}'
    payload = _feishu_encrypt(key, plain)
    # _aes_decrypt expects the IV as the first 16 chars of the string payload
    out = _aes_decrypt(key, payload)
    assert json.loads(out)["challenge"] == "abc123"


def test_aes_decrypt_matches_live_format():
    """Feishu sends base64(content) with the IV prepended as raw chars; the
    implementation slices payload[:16] as IV and base64-decodes the rest."""
    key = "k"
    plain = '{"type":"Event"}'
    # build exactly the wire shape: iv-as-characters + base64(ciphertext)
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = b"1234567890abcdef"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(hashlib.sha256(b"k").digest()), modes.CBC(iv))
    ct = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    wire = iv.decode("latin-1") + base64.b64encode(ct).decode("ascii")
    assert json.loads(_aes_decrypt(key, wire))["type"] == "Event"


class _FakeHub:
    def __init__(self):
        self.inbound = []

    def enqueue_inbound(self, message):
        self.inbound.append(message)


@pytest.mark.asyncio
async def test_feishu_event_mapping():
    hub = _FakeHub()
    channel = FeishuChannel({"receiveChatTypes": ["p2p", "group"]})
    channel.bind(hub)

    event = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "chat_id": "oc_abc123",
                "chat_type": "p2p",
                "content": json.dumps({"text": "你好 @_user_1"}),
            },
            "sender": {"sender_id": {"open_id": "ou_xyz"}},
        },
    }
    await channel._handle_event(event)
    assert len(hub.inbound) == 1
    msg = hub.inbound[0]
    assert msg.conversation_id == "oc_abc123"
    assert msg.text == "你好"  # <at> markup stripped (and whitespace trimmed)
    assert msg.sender == "ou_xyz"


@pytest.mark.asyncio
async def test_feishu_event_ignores_other_types_and_chats():
    hub = _FakeHub()
    channel = FeishuChannel({"receiveChatTypes": ["p2p"]})
    channel.bind(hub)

    # wrong event type
    await channel._handle_event({"header": {"event_type": "im.message.deleted"}, "event": {}})
    # chat type not in allowlist
    await channel._handle_event(
        {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {"chat_id": "g-1", "chat_type": "group", "content": json.dumps({"text": "hi"})}
            },
        }
    )
    assert hub.inbound == []
