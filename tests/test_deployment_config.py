"""Guard production deployment contracts without starting Docker in pytest."""

import json
import re
from pathlib import Path

import yaml

from app.nidin import NIDIN_API_BASE_URL

ROOT = Path(__file__).resolve().parent.parent
SERVICES = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]


def test_production_exposes_only_the_edge_proxy():
    published = {name for name, service in SERVICES.items() if service.get("ports")}
    assert published == {"nginx"}
    assert SERVICES["nginx"]["ports"] == ["${PORT:-6464}:80"]
    assert SERVICES["frontend"]["expose"] == ["80"]
    assert "http://frontend:80" in (ROOT / "nginx/default.conf.template").read_text()


def test_backend_preserves_existing_database_without_mounting_code():
    backend = SERVICES["nidin-menu-parser"]
    assert backend["volumes"] == ["./app/data:/app/app/data"]
    assert backend["environment"]["CART_DB_PATH"] == "/app/app/data/cart.db"
    assert backend["depends_on"]["data-init"]["condition"] == "service_completed_successfully"
    assert SERVICES["data-init"]["volumes"] == backend["volumes"]
    assert backend["build"]["target"] == "runner"
    assert not backend.get("command")
    runner = (ROOT / "Dockerfile").read_text().split("FROM runner AS tester")[0]
    assert "--reload" not in runner
    assert "COPY --chown=appuser:appuser app ./app" in runner


def test_frontend_starts_from_built_assets_without_installing_packages():
    frontend = SERVICES["frontend"]
    assert frontend["build"]["target"] == "runner"
    assert not frontend.get("volumes")
    assert not frontend.get("command")
    dockerfile = (ROOT / "frontend/Dockerfile").read_text()
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "COPY --from=builder /frontend/dist /usr/share/nginx/html" in dockerfile
    assert "try_files $uri $uri/ /index.html" in dockerfile
    runner = dockerfile.split(" AS runner", 1)[1]
    startup = (ROOT / "frontend/docker/entrypoint.sh").read_text()
    for text in (runner, startup):
        assert not re.search(r"\b(?:npm|pnpm|pip)\s+install\b", text)
        assert "vite" not in text
    package = json.loads((ROOT / "frontend/package.json").read_text())
    assert package["packageManager"].startswith("pnpm@")
    assert package["packageManager"] in dockerfile
    policy = yaml.safe_load((ROOT / "frontend/pnpm-workspace.yaml").read_text())
    assert policy["allowBuilds"]["esbuild"] is True
    assert "pnpm-workspace.yaml" in dockerfile
    documents = yaml.safe_load_all((ROOT / "frontend/pnpm-lock.yaml").read_text())
    locked = next(
        document["importers"]["."]
        for document in documents
        if "dependencies" in document.get("importers", {}).get(".", {})
    )
    for section in ("dependencies", "devDependencies"):
        for name, specifier in package[section].items():
            assert locked[section][name]["specifier"] == specifier


def test_default_upstream_matches_python_and_environment_example():
    expected = "https://loctw-service-api.nidin.shop/shopper/v2"
    configured = SERVICES["nidin-menu-parser"]["environment"]["NIDIN_MENU_API_BASE_URL"]
    assert configured == "${NIDIN_MENU_API_BASE_URL:-" + expected + "}"
    assert NIDIN_API_BASE_URL == expected
    sample = (ROOT / ".env.example").read_text().splitlines()
    assert "NIDIN_MENU_API_BASE_URL=" + expected in sample


def test_test_services_cannot_mount_the_production_database():
    for name in ("backend-test", "frontend-test"):
        service = SERVICES[name]
        assert service["profiles"] == ["test"]
        assert service["build"]["target"] == "tester"
        assert not service.get("volumes")
        assert not service.get("ports")
    assert SERVICES["backend-test"]["environment"]["CART_DB_PATH"].startswith("/tmp/")
    assert SERVICES["backend-test"]["tmpfs"] == ["/tmp"]


def test_python_dependencies_are_pinned():
    for filename in ("requirements.txt", "requirements-test.txt"):
        requirements = (ROOT / filename).read_text().splitlines()
        packages = [line for line in requirements if line and not line.startswith(("#", "-r "))]
        assert packages
        assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", line) for line in packages)


def test_backend_tester_uses_executable_scratch_for_subprocess_tests():
    tester = (ROOT / "Dockerfile").read_text().split("FROM runner AS tester", 1)[1]
    assert '"--basetemp=/home/appuser/pytest-tmp"' in tester
    assert "COPY frontend/src ./frontend/src" in tester


def test_tests_never_default_to_the_production_database():
    """主機上的 app/data/ 是正式容器掛載的資料目錄，測試一旦寫進去就是改正式資料。"""
    from app import cart_store

    production_dir = (Path(__file__).resolve().parent.parent / "app" / "data").resolve()
    assert production_dir not in cart_store.DB_PATH.resolve().parents


def test_nginx_passes_the_upstream_scheme_through_for_tunnels():
    """ngrok 等通道在前面做 TLS：nginx 若把 X-Forwarded-Proto 寫死成自己的 $scheme（http），
    後端會以為自己是 http，把瀏覽器的 https Origin 當成跨站請求一律回 403。"""
    template = (Path(__file__).resolve().parent.parent / "nginx" / "default.conf.template").read_text()

    assert "map ${DOLLAR}http_x_forwarded_proto ${DOLLAR}forwarded_proto" in template
    assert "X-Forwarded-Proto ${DOLLAR}scheme;" not in template
    assert template.count("X-Forwarded-Proto ${DOLLAR}forwarded_proto;") >= 1


def test_nginx_overwrites_x_forwarded_for_so_clients_cannot_spoof_their_ip():
    """後端用來源 IP 做登入與送單限流；nginx 若附加用戶端自帶的 X-Forwarded-For，任何人都能偽造 IP 繞過。"""
    template = (Path(__file__).resolve().parent.parent / "nginx" / "default.conf.template").read_text()

    assert "proxy_add_x_forwarded_for" not in template
    assert "X-Forwarded-For ${DOLLAR}remote_addr;" in template
