from pathlib import Path

from clashsub.cache_files import CacheFiles


def test_publish_is_immutable_and_readable_by_digest(tmp_path: Path):
    cache = CacheFiles(tmp_path)
    digest = cache.publish_raw(b"dmxlc3M6Ly9leGFtcGxl", {"profile-update-interval": "24"})
    snapshot = cache.read_raw(digest)
    assert snapshot.payload == b"dmxlc3M6Ly9leGFtcGxl"
    assert snapshot.safe_headers == {"profile-update-interval": "24"}
    assert not list(tmp_path.rglob("*.tmp"))


def test_prune_raw_keeps_recent_digests_and_current(tmp_path: Path):
    cache = CacheFiles(tmp_path)
    digests = [cache.publish_raw(f"payload-{index}".encode(), {}) for index in range(4)]

    cache.prune_raw({digests[-1]}, max_keep=3)

    remaining = {path.stem for path in (tmp_path / "raw").glob("*.bin")}
    assert digests[0] not in remaining
    assert set(digests[1:]) <= remaining
    assert not list((tmp_path / "raw").glob(f"{digests[0]}.*"))


def test_clear_converted_and_remove_converted_templates(tmp_path: Path):
    cache = CacheFiles(tmp_path)
    cache.write_converter_template("00000000-0000-0000-0000-000000000001", "a", "clash")
    cache.write_converter_template("00000000-0000-0000-0000-000000000001", "b", "surge")
    cache.write_converter_template("00000000-0000-0000-0000-000000000002", "c", "clash")

    cache.remove_converted("00000000-0000-0000-0000-000000000001")

    assert not list((tmp_path / "converted").glob("00000000-0000-0000-0000-000000000001*"))
    assert (tmp_path / "converted" / "00000000-0000-0000-0000-000000000002.yaml").exists()

    cache.clear_converted()

    assert not list((tmp_path / "converted").glob("*"))
