from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse


def register(app: FastAPI) -> None:
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        return templates.TemplateResponse(request, "settings.html", {"cfg": deps.cfg.public_view()})

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
        cfg.kindle.recipient = kindle_recipient.strip()
        cfg.smtp.host = smtp_host.strip()
        cfg.smtp.port = smtp_port
        cfg.smtp.user = smtp_user.strip()
        if smtp_password and smtp_password != "***":
            cfg.smtp.password = smtp_password.replace(" ", "").strip()
        cfg.pushover.enabled = bool(pushover_enabled)
        if pushover_user_key and pushover_user_key != "***":
            cfg.pushover.user_key = pushover_user_key.strip()
        if pushover_app_token and pushover_app_token != "***":
            cfg.pushover.app_token = pushover_app_token.strip()
        from endless_library.config import save_config

        save_config(cfg, request.app.state.config_path)
        return JSONResponse({"ok": True})

    @app.post("/api/settings/test-smtp", response_class=HTMLResponse)
    def test_smtp(request: Request):
        import asyncio
        from email.message import EmailMessage

        from endless_library.kindle import _send_smtp

        deps = request.app.state.deps
        cfg = deps.cfg
        if not cfg.smtp.host:
            return HTMLResponse('<span class="text-red-400">no SMTP host configured</span>')
        if not cfg.smtp.user or not cfg.smtp.password:
            return HTMLResponse('<span class="text-red-400">SMTP user + password required</span>')
        recipient = cfg.kindle.recipient or cfg.smtp.user
        msg = EmailMessage()
        msg["From"] = cfg.smtp.user
        msg["To"] = recipient
        msg["Subject"] = "endless-library SMTP test"
        msg.set_content(
            "If you see this, the endless-library -> Kindle pipeline can reach the inbox.\n"
            "The doc title on Kindle will be whatever the message Subject is."
        )
        try:
            r = asyncio.run(_send_smtp(msg, smtp=cfg.smtp, timeout=20.0))
            return HTMLResponse(
                f'<span class="text-emerald-400">OK to {recipient}</span> '
                f'<span class="text-slate-500">{str(r.response)[:140]}</span>'
            )
        except Exception as e:
            return HTMLResponse(f'<span class="text-red-400">FAIL: {type(e).__name__}: {e}</span>')

    @app.post("/api/settings/test-pushover", response_class=HTMLResponse)
    def test_pushover(request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        if not cfg.pushover.user_key or not cfg.pushover.app_token:
            return HTMLResponse('<span class="text-red-400">Pushover keys not configured</span>')
        # Force enable for the test even if globally off
        prev = cfg.pushover.enabled
        cfg.pushover.enabled = True
        try:
            ok = deps.notifier._send("endless-library test", "Pushover works")
        finally:
            cfg.pushover.enabled = prev
        if ok:
            return HTMLResponse('<span class="text-emerald-400">OK - check your device</span>')
        return HTMLResponse('<span class="text-red-400">Pushover send failed (see logs)</span>')
