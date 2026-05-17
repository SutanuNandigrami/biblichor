from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse


def register(app: FastAPI) -> None:
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"request": request, "cfg": deps.cfg.public_view()},
        )

    @app.post("/api/settings")
    def save(
        request: Request,
        poll_interval_minutes: int = Form(...),
        max_attempts: int = Form(...),
        kindle_recipient: str = Form(""),
        smtp_host: str = Form(""),
        smtp_port: int = Form(587),
        smtp_user: str = Form(""),
        smtp_password: str = Form(""),
        pushover_enabled: str = Form(""),
        pushover_user_key: str = Form(""),
        pushover_app_token: str = Form(""),
        auto_pick_threshold: float = Form(70),
        auto_pick_gap: float = Form(10),
        log_level: str = Form("INFO"),
    ):
        deps = request.app.state.deps
        cfg = deps.cfg
        cfg.general.poll_interval_minutes = poll_interval_minutes
        cfg.general.max_attempts = max_attempts
        cfg.general.auto_pick_threshold = auto_pick_threshold
        cfg.general.auto_pick_gap = auto_pick_gap
        cfg.general.log_level = log_level
        cfg.kindle.recipient = kindle_recipient
        cfg.smtp.host = smtp_host
        cfg.smtp.port = smtp_port
        cfg.smtp.user = smtp_user
        if smtp_password and smtp_password != "***":
            cfg.smtp.password = smtp_password
        cfg.pushover.enabled = bool(pushover_enabled)
        if pushover_user_key and pushover_user_key != "***":
            cfg.pushover.user_key = pushover_user_key
        if pushover_app_token and pushover_app_token != "***":
            cfg.pushover.app_token = pushover_app_token
        from endless_library.config import save_config

        save_config(cfg, request.app.state.config_path)
        return JSONResponse({"ok": True})

    @app.post("/api/settings/test-smtp")
    def test_smtp(request: Request):
        deps = request.app.state.deps
        import asyncio
        from email.message import EmailMessage

        from endless_library.kindle import _send_smtp

        msg = EmailMessage()
        msg["From"] = deps.cfg.smtp.user or "endless-library@localhost"
        msg["To"] = deps.cfg.kindle.recipient or msg["From"]
        msg["Subject"] = "endless-library SMTP test"
        msg.set_content("If you see this, SMTP works.")
        try:
            r = asyncio.run(_send_smtp(msg, smtp=deps.cfg.smtp, timeout=15.0))
            return {"ok": True, "response": r.response}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/settings/test-pushover")
    def test_pushover(request: Request):
        deps = request.app.state.deps
        ok = deps.notifier._send("endless-library test", "Pushover works ✓")
        return {"ok": ok}
