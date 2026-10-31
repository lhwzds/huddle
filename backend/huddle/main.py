"""Application wiring and lifespan."""
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .api import router
from .agents import AgentRegistry
from .config import default_group, load_config
from .guards import AccessMiddleware, Guards, LimitError
from .llm import make_llm
from .metering import TokenMeter
from .orchestrator import Orchestrator
from .store import Store
from .tools import Sandbox


async def limit_error(request, exc: LimitError):
    """The answer of a refused request: a stable detail and a retry hint."""
    body = {'detail': exc.detail}
    headers = {}
    if exc.retry_after is not None:
        body['retry_after'] = exc.retry_after
        headers['Retry-After'] = str(int(exc.retry_after))
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

def create_app(cfg=None, *, store=None, llm_factory=None, sandbox=None) -> FastAPI:
    cfg = cfg if cfg is not None else load_config()
    store = store if store is not None else Store(cfg['database'], default_group=default_group(cfg))
    owns_sandbox = sandbox is None
    sandbox = sandbox if sandbox is not None else Sandbox(cfg['sandbox_url'],
                                                          margin_s=cfg['limits']['sandbox_request_margin_s'])
    factory = llm_factory if llm_factory is not None else make_llm
    registry = AgentRegistry(store, cfg)
    # Every model call is metered, so the daily budget knows what it costs.
    meter = TokenMeter(store)

    @asynccontextmanager
    async def lifespan(app):
        def check_keys(model_keys):
            for model_key in model_keys:
                check_model(model_key)

        def check_model(model_key):
            if model_key not in cfg['models']:
                raise RuntimeError(f'Unknown model key: {model_key}')
            model_cfg = cfg['models'][model_key]
            key_file = model_cfg.get('api_key_file')
            key_env = model_cfg.get('api_key_env')
            if key_file:
                path = Path(str(key_file)).expanduser()
                try:
                    content = path.read_text(encoding='utf-8').strip()
                except OSError:
                    content = ''
                if not content:
                    raise RuntimeError(f'Missing API key file: {path}')
            elif key_env and not os.environ.get(key_env):
                raise RuntimeError(f'Missing API key environment variable: {key_env}')

        check_keys({cfg['model']})
        await store.open()
        try:
            await store.stop_streaming_on_startup()
            await registry.load()
            # Members stored in the database may name a model of their own: check those too.
            check_keys({a.model for g in registry.groups() for a in registry.all(g['key']) if a.model})
            yield
        finally:
            await app.state.orchestrator.close()
            await store.close()
            if owns_sandbox:
                await sandbox.close()

    app = FastAPI(lifespan=lifespan)
    app.state.cfg = cfg
    app.state.store = store
    app.state.sandbox = sandbox
    app.state.registry = registry
    app.state.meter = meter
    app.state.guards = Guards(store)
    app.state.orchestrator = Orchestrator(cfg, store, factory, sandbox, meter=meter, registry=registry)
    app.add_middleware(AccessMiddleware, guards=app.state.guards)
    app.add_exception_handler(LimitError, limit_error)
    app.include_router(router)
    return app
