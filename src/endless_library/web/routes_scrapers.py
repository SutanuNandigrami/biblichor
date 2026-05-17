from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse


def register(app: FastAPI) -> None:
    @app.get("/scrapers", response_class=HTMLResponse)
    def scrapers_page(request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        cfg = deps.cfg
        from endless_library.scrapers import registry

        all_names = registry.available()
        stats = {n: deps.bench.success_rate(scraper=n) for n in all_names}
        return templates.TemplateResponse(
            request,
            "scrapers.html",
            {"request": request, "all_scrapers": all_names, "cfg": cfg, "stats": stats},
        )

    @app.post("/api/scrapers/{name}/toggle")
    def toggle(name: str, request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        cfg.scrapers.enabled[name] = not cfg.scrapers.enabled.get(name, False)
        from endless_library.config import save_config

        save_config(cfg, request.app.state.config_path)
        return {"ok": True, "enabled": cfg.scrapers.enabled[name]}

    @app.post("/api/scrapers/order")
    def reorder(request: Request, order: str = Form(...)):
        deps = request.app.state.deps
        names = [n.strip() for n in order.split(",") if n.strip()]
        deps.cfg.scrapers.order = names
        from endless_library.config import save_config

        save_config(deps.cfg, request.app.state.config_path)
        return {"ok": True, "order": names}

    @app.post("/api/bench/run")
    async def run_bench(request: Request, mode: str = "quick"):
        import asyncio
        from pathlib import Path

        from endless_library.bench import format_table, load_queries, run_bench

        deps = request.app.state.deps
        bench_path = Path("bench/queries.yaml")
        qs, quick_idx = load_queries(bench_path)
        if mode == "quick":
            qs = [qs[i] for i in quick_idx if i < len(qs)]
        outcomes = await asyncio.to_thread(run_bench, deps.cfg, qs, deps.bench)
        return {
            "ok": True,
            "outcomes": [
                {
                    "scraper": o.scraper,
                    "query": o.query,
                    "success": o.success,
                    "duration_ms": o.duration_ms,
                    "candidates": o.candidates,
                    "note": o.note,
                }
                for o in outcomes
            ],
            "table": format_table(outcomes),
        }
