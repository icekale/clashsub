from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compose_allows_fallback_only_without_airport_secret_files():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    environment = compose["services"]["clashsub"]["environment"]
    assert environment["AIRPORT_API_BASE_URL"] == "${AIRPORT_API_BASE_URL:-}"
    assert environment["AIRPORT_EMAIL_FILE"] == "/run/secrets/airport_email"
    assert environment["AIRPORT_PASSWORD_FILE"] == "/run/secrets/airport_password"

    secrets = compose["secrets"]
    assert secrets["airport_email"]["file"] == "${AIRPORT_EMAIL_SECRET_FILE:-/dev/null}"
    assert secrets["airport_password"]["file"] == "${AIRPORT_PASSWORD_SECRET_FILE:-/dev/null}"
    assert secrets["upstream_url"]["file"] == "${UPSTREAM_URL_SECRET_FILE:-./secrets/upstream_url}"
    assert environment["OPENCLASH_SSH_KEY_FILE"] == "/run/secrets/openclash_ssh_key"
    assert secrets["openclash_ssh_key"]["file"] == "${OPENCLASH_SSH_KEY_SECRET_FILE:-/dev/null}"
    ssh_secret = next(
        item
        for item in compose["services"]["clashsub"]["secrets"]
        if isinstance(item, dict) and item.get("source") == "openclash_ssh_key"
    )
    assert ssh_secret["uid"] == "10001"
    assert ssh_secret["gid"] == "10001"
    assert ssh_secret["mode"] in {0o400, 400}


def test_compose_runs_converter_inside_the_single_container():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "subconverter" not in compose["services"]
    clashsub = compose["services"]["clashsub"]
    assert "depends_on" not in clashsub
    assert clashsub["environment"]["CONVERTER_BASE_URL"] == "http://127.0.0.1:25500"
    assert clashsub["environment"]["CONVERTER_SOURCE_BASE_URL"] == "http://127.0.0.1:8080"
    assert clashsub["cap_drop"] == ["ALL"]
    assert clashsub["security_opt"] == ["no-new-privileges:true"]
    assert clashsub["read_only"] is True

    healthcheck = " ".join(clashsub["healthcheck"]["test"])
    assert "8080/healthz" in healthcheck
    assert "25500/version" in healthcheck


def test_dockerfile_merges_converter_sidecar_into_runtime_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM aethersailor/subconverter-extended:v1.9.4 AS converter" in dockerfile
    assert "FROM python:3.13-slim-trixie AS runtime" in dockerfile
    assert "COPY --from=converter /usr/bin/subconverter /usr/bin/subconverter" in dockerfile
    assert "COPY --from=converter /usr/lib/libmihomo.so /usr/lib/libmihomo.so" in dockerfile
    assert "COPY --from=converter /base /base" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "docker-entrypoint.sh" in dockerfile
    assert dockerfile.index("COPY src/") > dockerfile.index("pip install")
    assert "pypi.tuna.tsinghua.edu.cn" in dockerfile


def test_entrypoint_starts_sidecar_then_execs_main_command():
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "start-subconverter" in entrypoint
    assert "cp -a /base/. /tmp/subconverter/" in entrypoint
    assert "PREF_PATH=/tmp/subconverter/pref.toml" in entrypoint
    assert "127.0.0.1:25500/version" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_entrypoint_derives_pref_from_image_example_with_assertions():
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "awk -f /usr/local/bin/pref-patch.awk /base/pref.example.toml" in entrypoint
    assert "COPY pref-patch.awk /usr/local/bin/pref-patch.awk" in dockerfile


def test_pref_patch_overrides_only_the_expected_keys(tmp_path):
    source = tmp_path / "pref.example.toml"
    source.write_text(
        "[managed_config]\nwrite_managed_config = true\n"
        "[remote_subscription]\nsurge_policy_path = true\nsurfboard_policy_path = true\nloon_remote_proxy = true\n"
        "[statistics]\nenabled = false\ndata_dir = \"stats\"\nflush_interval = 5\n"
        "[security]\nprofile = \"lan\"\nallow_public_upload = false\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["awk", "-f", str(ROOT / "pref-patch.awk"), str(source)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "write_managed_config = false" in result.stdout
    assert "surge_policy_path = false" in result.stdout
    assert "surfboard_policy_path = false" in result.stdout
    assert "loon_remote_proxy = false" in result.stdout
    assert "enabled = true" in result.stdout
    assert 'data_dir = "/data/stats"' in result.stdout
    # 未被覆写的键原样保留
    assert "flush_interval = 5" in result.stdout
    assert "allow_public_upload = false" in result.stdout
    assert 'profile = "lan"' in result.stdout


def test_pref_patch_fails_when_an_expected_key_disappears(tmp_path):
    source = tmp_path / "pref.example.toml"
    source.write_text("[managed_config]\nwrite_managed_config = true\n", encoding="utf-8")
    result = subprocess.run(
        ["awk", "-f", str(ROOT / "pref-patch.awk"), str(source)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "remote_subscription.surge_policy_path" in result.stderr


def test_readme_prompts_for_secret_values_without_literal_password_examples():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "read -r -s" in readme
    assert "AIRPORT_EMAIL_SECRET_FILE" in readme
    assert "AIRPORT_PASSWORD_SECRET_FILE" in readme
    assert "ghcr.io/icekale/clashsub" in readme
    assert "replace-with-airport-password" not in readme
    assert "replace-with-a-long-random-password" not in readme
    assert "subscription/REPLACE_ME" not in readme


def test_smoke_uses_https_fixture_compatible_with_download_policy():
    smoke = (ROOT / "scripts" / "smoke.sh").read_text(encoding="utf-8")

    assert "https://fixture.example.test/sample_base64.txt" in smoke
    assert "smoke_app.py" in smoke
    assert '"http://$fixture:8000/sample_base64.txt"' not in smoke


def test_runtime_image_and_compose_use_asia_shanghai_timezone():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "tzdata" in dockerfile
    assert "openssh-client" in dockerfile
    assert "TZ=Asia/Shanghai" in dockerfile
    assert compose["services"]["clashsub"]["environment"]["TZ"] == "Asia/Shanghai"


def test_verify_script_exists_with_core_health_checks():
    script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

    assert "healthz" in script
    assert "overview" in script
    assert "logs" in script
    assert "cache" in script


def test_backup_script_exists_with_safe_snapshot_flow():
    script = (ROOT / "scripts" / "backup-and-verify.sh").read_text(encoding="utf-8")

    assert "sqlite3" in script and ".backup" in script
    assert "verify.sh" in script
    assert "state.db" in script
    assert "18083" not in script
    assert 'verify.sh" "${1:-http://' not in script
