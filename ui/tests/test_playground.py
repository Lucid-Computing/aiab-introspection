from fastapi.testclient import TestClient

from server.auth import Account


def test_playground_requires_login_and_serves_only_its_build(tmp_path, monkeypatch):
    from server import app as web

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>Audit plan playground</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("outside")
    monkeypatch.setattr(web, "PLAYGROUND_DIR", tmp_path)
    client = TestClient(web.app)

    page = client.get("/audit-plan-playground/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")

    client.cookies.set(web.COOKIE, web.signer.issue(Account("author", "lab", "Author", "unused")))
    assert "Audit plan playground" in client.get("/audit-plan-playground/").text
    assert "Audit plan playground" in client.get("/audit-plan-playground").text
    assert client.get("/audit-plan-playground/assets/app.js").text == "console.log(1)"
    assert client.get("/audit-plan-playground/missing.js").status_code == 404
    assert client.get("/audit-plan-playground/..%2Fsecret.txt").status_code == 404


def test_playground_unbuilt_says_how_to_build(tmp_path, monkeypatch):
    from server import app as web

    monkeypatch.setattr(web, "PLAYGROUND_DIR", tmp_path / "dist")
    client = TestClient(web.app)
    client.cookies.set(web.COOKIE, web.signer.issue(Account("author", "lab", "Author", "unused")))
    response = client.get("/audit-plan-playground/")
    assert response.status_code == 404 and "pnpm build" in response.text
