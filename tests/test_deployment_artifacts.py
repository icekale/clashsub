from pathlib import Path

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

    assert "FROM aethersailor/subconverter-extended:v1.2.0 AS converter" in dockerfile
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


def test_readme_prompts_for_secret_values_without_literal_password_examples():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "read -r -s" in readme
    assert "AIRPORT_EMAIL_SECRET_FILE" in readme
    assert "AIRPORT_PASSWORD_SECRET_FILE" in readme
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
