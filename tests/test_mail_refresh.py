import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clashsub.app import create_app
from clashsub.config import Settings
from clashsub.mail_refresh import MailRefreshWatcher, is_risk_mail, parse_from_subject
from clashsub.settings import RuntimeSettings


def test_is_risk_mail_matches_bitznet_sample():
    assert is_risk_mail(
        "Bitz Net <postmaster@bitznet.app>",
        "您的 BitzNet 账户被风控",
    )


def test_is_risk_mail_requires_bitznet_from():
    assert not is_risk_mail("alerts@example.com", "您的 BitzNet 账户被风控")


def test_is_risk_mail_requires_subject_needle():
    assert not is_risk_mail("postmaster@bitznet.app", "BitzNet 账单")


def test_parse_from_subject_reads_sample_headers():
    raw = (
        "From: Bitz Net <postmaster@bitznet.app>\r\n"
        "Subject: 您的 BitzNet 账户被风控\r\n\r\n"
    ).encode("utf-8")
    assert parse_from_subject(raw) == (
        "Bitz Net <postmaster@bitznet.app>",
        "您的 BitzNet 账户被风控",
    )


@pytest.mark.asyncio
async def test_poll_skips_when_disabled():
    fetches = []

    async def fetch(after_uid):
        fetches.append(after_uid)
        return []

    watcher = MailRefreshWatcher(
        enabled=lambda: False,
        username=lambda: "user@qq.com",
        auth=lambda: "auth-code",
        fetch_messages=fetch,
        refresh=_fail_refresh,
        load_uid=lambda: None,
        store_uid=lambda uid: None,
    )
    await watcher.poll()
    assert fetches == []


@pytest.mark.asyncio
async def test_first_poll_records_max_uid_without_refresh():
    stored = []
    refreshes = []

    async def fetch(after_uid):
        assert after_uid is None
        return [(5, "postmaster@bitznet.app", "您的 BitzNet 账户被风控")]

    async def refresh():
        refreshes.append(1)

    watcher = MailRefreshWatcher(
        enabled=lambda: True,
        username=lambda: "user@qq.com",
        auth=lambda: "auth-code",
        fetch_messages=fetch,
        refresh=refresh,
        load_uid=lambda: None,
        store_uid=stored.append,
    )
    await watcher.poll()
    assert stored == [5]
    assert refreshes == []


@pytest.mark.asyncio
async def test_new_risk_mail_triggers_refresh_once():
    stored = [5]
    refreshes = []
    inbox = [(6, "Bitz Net <postmaster@bitznet.app>", "您的 BitzNet 账户被风控")]

    async def fetch(after_uid):
        return [item for item in inbox if item[0] > (after_uid or 0)]

    async def refresh():
        refreshes.append(1)

    watcher = MailRefreshWatcher(
        enabled=lambda: True,
        username=lambda: "user@qq.com",
        auth=lambda: "auth-code",
        fetch_messages=fetch,
        refresh=refresh,
        load_uid=lambda: stored[-1],
        store_uid=stored.append,
        now=lambda: 1000.0,
    )
    await watcher.poll()
    await watcher.poll()
    assert refreshes == [1]
    assert stored[-1] == 6


@pytest.mark.asyncio
async def test_non_risk_mail_does_not_refresh():
    stored = [5]
    refreshes = []

    async def fetch(after_uid):
        return [(7, "postmaster@bitznet.app", "BitzNet 账单")]

    async def refresh():
        refreshes.append(1)

    watcher = MailRefreshWatcher(
        enabled=lambda: True,
        username=lambda: "user@qq.com",
        auth=lambda: "auth-code",
        fetch_messages=fetch,
        refresh=refresh,
        load_uid=lambda: stored[-1],
        store_uid=stored.append,
    )
    await watcher.poll()
    assert refreshes == []
    assert stored[-1] == 7


@pytest.mark.asyncio
async def test_cooldown_skips_second_risk_mail():
    stored = [5]
    refreshes = []
    clock = {"t": 1000.0}

    async def fetch(after_uid):
        return [
            item
            for item in (
                (6, "postmaster@bitznet.app", "您的 BitzNet 账户被风控"),
                (7, "postmaster@bitznet.app", "您的 BitzNet 账户被风控"),
            )
            if item[0] > (after_uid or 0)
        ]

    async def refresh():
        refreshes.append(clock["t"])

    watcher = MailRefreshWatcher(
        enabled=lambda: True,
        username=lambda: "user@qq.com",
        auth=lambda: "auth-code",
        fetch_messages=fetch,
        refresh=refresh,
        load_uid=lambda: stored[-1],
        store_uid=stored.append,
        now=lambda: clock["t"],
        cooldown_seconds=600,
    )
    await watcher.poll()
    clock["t"] = 1100.0
    await watcher.poll()
    assert len(refreshes) == 1


async def _fail_refresh():
    raise AssertionError("refresh should not run")


def _mail_app_settings(tmp_path: Path):
    key = tmp_path / "key"
    key.write_text(base64.b64encode(b"k" * 32).decode(), encoding="ascii")
    return Settings(
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        upstream_url=SecretStr("https://provider.invalid/sub?token=hidden"),
        initial_username=SecretStr("user"),
        initial_password=SecretStr("pass"),
        airport_email=SecretStr("member@qq.com"),
        encryption_key_file=key,
    )


def test_mail_credentials_and_setting_roundtrip(tmp_path):
    app = create_app(_mail_app_settings(tmp_path), start_scheduler=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        client.app.state.services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver")
        )
        login = client.post("/api/auth/login", json={"username": "user", "password": "pass"})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.get("/api/admin/mail/credentials").json() == {"configured": False}
        saved = client.put(
            "/api/admin/mail/credentials",
            headers=headers,
            json={"secret": "imap-auth-code"},
        )
        assert saved.status_code == 200
        assert client.get("/api/admin/mail/credentials").json() == {"configured": True}
        current = client.get("/api/admin/settings").json()
        assert current["mail_refresh_enabled"] is False
        updated = client.put(
            "/api/admin/settings",
            headers=headers,
            json={**current, "mail_refresh_enabled": True},
        )
        assert updated.status_code == 200
        assert client.get("/api/admin/settings").json()["mail_refresh_enabled"] is True
