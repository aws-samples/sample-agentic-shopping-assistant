import server


def test_keep_alive_outlasts_the_load_balancer_idle_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.setenv("PORT", "8080")
    server.main()
    [(args, kwargs)] = calls
    assert args == ("main:app",)
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8080
    # The ALB idle timeout is 150 s (CommonApp stack); ECS kills a task 30 s after SIGTERM.
    assert kwargs["timeout_keep_alive"] > 150
    assert 0 < kwargs["timeout_graceful_shutdown"] < 30
