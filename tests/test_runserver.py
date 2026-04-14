import runserver


class FakeApp:
    def __init__(self) -> None:
        self.config = {"DEBUG": False}
        self.run_kwargs: dict[str, object] | None = None

    def run(self, **kwargs: object) -> None:
        self.run_kwargs = kwargs


def test_main_binds_to_loopback_by_default(monkeypatch) -> None:
    app = FakeApp()

    monkeypatch.delenv("HOST_IP", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(runserver, "create_app", lambda: app)

    runserver.main()

    assert app.run_kwargs == {"debug": False, "host": "127.0.0.1", "port": 5000}
