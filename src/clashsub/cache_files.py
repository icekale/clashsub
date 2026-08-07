from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawSnapshot:
    payload: bytes
    safe_headers: dict[str, str]


class CacheFiles:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """fsync 目录本身，确保 os.replace 的重命名在断电后仍然持久。"""
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def publish_raw(self, payload: bytes, safe_headers: dict[str, str]) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        self._atomic_write(self.root / "raw" / f"{digest}.bin", payload)
        metadata = json.dumps(safe_headers, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._atomic_write(self.root / "raw" / f"{digest}.json", metadata)
        return digest

    def read_raw(self, digest: str) -> RawSnapshot:
        payload = (self.root / "raw" / f"{digest}.bin").read_bytes()
        try:
            headers = json.loads((self.root / "raw" / f"{digest}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 元数据文件缺失/损坏不影响订阅内容本身，降级为空头。
            headers = {}
        return RawSnapshot(payload, headers)

    def prune_raw(self, keep_digests: set[str], max_keep: int = 3) -> None:
        raw_dir = self.root / "raw"
        try:
            files = sorted(
                raw_dir.glob("*.bin"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        keep = set(keep_digests)
        keep.update(path.stem for path in files[:max_keep])
        for path in files:
            if path.stem in keep:
                continue
            for candidate in (path, path.with_suffix(".json")):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass

    def _converter_path(self, share_id: str, format: str = "clash") -> Path:
        normalized = str(uuid.UUID(share_id))
        if format == "clash":
            return self.root / "converted" / f"{normalized}.yaml"
        extension = "yaml" if format == "clash" else "conf"
        return self.root / "converted" / f"{normalized}-{format}.{extension}"

    def write_converter_template(self, share_id: str, text: str, format: str = "clash") -> None:
        self._atomic_write(self._converter_path(share_id, format), text.encode("utf-8"))

    def read_converter_template(self, share_id: str, format: str = "clash") -> str:
        return self._converter_path(share_id, format).read_text(encoding="utf-8")

    def converter_mtime(self, share_id: str, format: str = "clash") -> float:
        return self._converter_path(share_id, format).stat().st_mtime

    def remove_converted(self, share_id: str) -> None:
        normalized = str(uuid.UUID(share_id))
        converted = self.root / "converted"
        try:
            for path in converted.glob(f"{normalized}*"):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass

    def clear_converted(self) -> None:
        converted = self.root / "converted"
        try:
            for path in converted.iterdir():
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass
