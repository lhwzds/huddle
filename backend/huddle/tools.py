"""Sandbox HTTP client and bounded, fault-tolerant tool dispatch."""
import json
from dataclasses import dataclass

import httpx

from . import prompts, web
from .llm import ToolCall, ToolSpec

class FileUnavailable(Exception):
    """A file the sandbox does not have (404) or will not show (400: path not allowed, too large)."""

    def __init__(self, status, reason=""):
        super().__init__(status, reason)
        self.status, self.reason = status, reason


class Sandbox:
    def __init__(self, base_url, *, client=None, margin_s=10):
        # HTTP waits for the command's own timeout plus a margin (config limits.sandbox_request_margin_s).
        self.margin_s = margin_s
        self.client = client if client is not None else httpx.AsyncClient(base_url=base_url.rstrip("/"),
                                                                          timeout=margin_s * 3)

    async def exec(self, workspace, cmd, timeout_s):
        response = await self.client.post('/exec', json=dict(workspace=workspace, cmd=cmd, timeout_s=timeout_s),
                                          timeout=timeout_s + self.margin_s)
        response.raise_for_status()
        return response.json()

    async def kill(self, workspace):
        response = await self.client.post('/kill', json=dict(workspace=workspace))
        response.raise_for_status()
        return response.json()['killed']

    async def files(self, workspace):
        response = await self.client.get('/files', params=dict(workspace=workspace))
        response.raise_for_status()
        return response.json()

    async def read(self, workspace, path):
        response = await self.client.get('/file', params=dict(workspace=workspace, path=path))
        if response.status_code in (400, 404):
            # The sandbox answered: the file is not there, or it will not show it (and says why).
            try:
                reason = str(response.json().get("error", ""))
            except ValueError:
                reason = ""
            raise FileUnavailable(response.status_code, reason)
        response.raise_for_status()
        return response.json()

    async def write(self, workspace, path, content):
        response = await self.client.put('/file', json=dict(workspace=workspace, path=path, content=content))
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()


# Tool schemas contain no execution logic; all calls stay inside the sandbox.
def _spec(name, fields):
    return ToolSpec(name, prompts.TOOL_DESCRIPTIONS[name], {
        'type': 'object', 'properties': {key: {'type': 'string'} for key in fields},
        'required': fields, 'additionalProperties': False,
    })


TOOL_SPECS = [_spec('bash', ['cmd']), _spec('write_file', ['path', 'content']), _spec('read_file', ['path']),
              _spec('web_search', ['query']), _spec('fetch_url', ['url'])]
# Offered at every effort level, at most once per message; it edits the agent's memory, not the sandbox.
REMEMBER_SPEC = _spec('remember', ['note'])


@dataclass(frozen=True)
class ToolSettings:
    """What run_tool needs from config (limits and the web search endpoint)."""
    bash_timeout_s: int
    result_chars: int
    page_result_chars: int
    page_fetch_chars: int
    search_url: str

    @classmethod
    def from_cfg(cls, cfg):
        limits = cfg["limits"]
        return cls(bash_timeout_s=limits["bash_timeout_s"], result_chars=limits["tool_result_chars"],
                   page_result_chars=limits["page_result_chars"], page_fetch_chars=limits["page_fetch_chars"],
                   search_url=cfg["web"]["search_url"])


_REQUIRED = {spec.name: spec.parameters["required"] for spec in TOOL_SPECS}


async def run_tool(sandbox, workspace, call: ToolCall, settings: ToolSettings) -> tuple[str, str]:
    """Return bounded model output and a terse UI summary, including on failure."""
    summary = call.name
    try:
        args = json.loads(call.arguments)
        if not isinstance(args, dict):
            raise ValueError(prompts.TOOL_ERRORS['object'])
        if call.name not in _REQUIRED:
            raise ValueError(prompts.unknown_tool(call.name))
        if any(not isinstance(args.get(key), str) for key in _REQUIRED[call.name]):
            raise ValueError(prompts.TOOL_ERRORS['strings'])
        if call.name == 'bash':
            summary = f"ran `{args['cmd'][:60]}`"
            result = await sandbox.exec(workspace, args['cmd'], settings.bash_timeout_s)
        elif call.name == 'write_file':
            summary = f"wrote {args['path']}"
            result = await sandbox.write(workspace, args['path'], args['content'])
        elif call.name == 'read_file':
            summary = f"read {args['path']}"
            result = await sandbox.read(workspace, args['path'])
        elif call.name == 'web_search':
            summary = f"searched the web for “{args['query'][:50]}”"
            hits = await web.web_search(args['query'], search_url=settings.search_url)
            path, note = web.research_note('search', args['query'], hits)
            await sandbox.write(workspace, path, note)
            result = {'results': hits, 'saved_to': path}
        else:
            # Web pages are read by the backend (the sandbox has no network) and kept as research notes.
            page = await web.fetch_url(args['url'], max_chars=settings.page_fetch_chars)
            path, note = web.research_note('page', page['url'], page)
            await sandbox.write(workspace, path, note)
            summary = f"read {page['url'][:60]} → {path}"
            result = {'title': page['title'], 'text': page['text'][:settings.page_result_chars],
                      'truncated': page['truncated'], 'saved_to': path}
        text = json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        text = prompts.tool_error(str(exc))
    return text[:settings.result_chars], summary
