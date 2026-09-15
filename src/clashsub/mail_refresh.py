import logging
import time
from email.header import decode_header
from email.parser import Parser

logger = logging.getLogger("clashsub.mail_refresh")

FROM_NEEDLE = "bitznet.app"
SUBJECT_NEEDLE = "被风控"
IMAP_HOST = "imap.qq.com"
POLL_SECONDS = 60
COOLDOWN_SECONDS = 600
SECRET_NAME = "qq_imap_auth"
LAST_UID_KEY = "mail_refresh_last_uid"


def is_risk_mail(from_addr: str, subject: str) -> bool:
    return FROM_NEEDLE in (from_addr or "").lower() and SUBJECT_NEEDLE in (subject or "")


def decode_header_value(value: str) -> str:
    chunks = []
    for part, charset in decode_header(value or ""):
        if isinstance(part, bytes):
            chunks.append(part.decode(charset or "utf-8", "replace"))
        else:
            chunks.append(part)
    return "".join(chunks)


def parse_from_subject(header_bytes: bytes) -> tuple[str, str]:
    message = Parser().parsestr(header_bytes.decode("utf-8", "replace"), headersonly=True)
    return decode_header_value(message.get("From", "")), decode_header_value(message.get("Subject", ""))


def _header_payload(fetched) -> bytes:
    for item in fetched or ():
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def fetch_imap_headers(username: str, password: str, after_uid: int | None, host: str = IMAP_HOST) -> list[tuple[int, str, str]]:
    import imaplib

    client = imaplib.IMAP4_SSL(host)
    try:
        status, _ = client.login(username, password)
        if status != "OK":
            raise RuntimeError("imap login failed")
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("imap select failed")
        criterion = "ALL" if after_uid is None else f"UID {int(after_uid) + 1}:*"
        status, data = client.uid("search", None, criterion)
        if status != "OK" or not data or not data[0]:
            return []
        uids = [int(uid) for uid in data[0].split() if uid]
        if after_uid is not None:
            uids = [uid for uid in uids if uid > int(after_uid)]
        if not uids:
            return []
        if after_uid is None:
            return [(max(uids), "", "")]
        messages = []
        for uid in uids:
            status, fetched = client.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK":
                continue
            from_addr, subject = parse_from_subject(_header_payload(fetched))
            messages.append((uid, from_addr, subject))
        return messages
    finally:
        try:
            client.logout()
        except Exception:
            pass


class MailRefreshWatcher:
    def __init__(
        self,
        *,
        enabled,
        username,
        auth,
        fetch_messages,
        refresh,
        load_uid,
        store_uid,
        now=time.time,
        cooldown_seconds=COOLDOWN_SECONDS,
    ):
        self.enabled = enabled
        self.username = username
        self.auth = auth
        self.fetch_messages = fetch_messages
        self.refresh = refresh
        self.load_uid = load_uid
        self.store_uid = store_uid
        self.now = now
        self.cooldown_seconds = cooldown_seconds
        self._last_refresh_at = 0.0

    async def poll(self) -> None:
        if not self.enabled() or not self.username() or not self.auth():
            return
        last = self.load_uid()
        messages = await self.fetch_messages(last)
        if last is None:
            self.store_uid(max((uid for uid, _, _ in messages), default=0))
            return
        last_uid = int(last)
        for uid, from_addr, subject in sorted(messages, key=lambda item: item[0]):
            if uid <= last_uid:
                continue
            self.store_uid(uid)
            last_uid = uid
            if not is_risk_mail(from_addr, subject):
                continue
            now = self.now()
            if self._last_refresh_at and now - self._last_refresh_at < self.cooldown_seconds:
                logger.info("mail refresh skipped cooldown uid=%s", uid)
                continue
            await self.refresh()
            self._last_refresh_at = now
            logger.info("mail refresh triggered uid=%s", uid)
