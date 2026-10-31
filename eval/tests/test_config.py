"""The harness shares the server's configuration code instead of copying it."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import huddle.config
import huddle_eval.core as core
import huddle_eval.runner as runner
from huddle_eval.core import configured_message_chars, load_cases
from huddle_eval.prompts import JUDGE_SYSTEM
from huddle_eval.runner import (api_key, config_model, default_base, default_judge,
                                judge_key, judge_setup, judge_statement, load_config,
                                params_for)

CONFIG = """
model: judge
models:
  judge:
    provider: openai_chat
    base_url: ${JUDGE_BASE_URL:-http://judge.test/v1}
    model: judge-model
    api_key_env: JUDGE_KEY
    params:
      default: {reasoning_effort: minimal}
      deep: {reasoning_effort: low}
limits:
  message_chars: 12
"""


def test_runner_uses_the_backend_config_code():
    # The interpolation and the profile params are the backend's, not a copy.
    assert runner.load_config is huddle.config.load_config
    assert runner.params_for is huddle.config.params_for
    assert not hasattr(runner, 'interpolate')
    assert not hasattr(runner, '_interpolate')
    assert not hasattr(core, 'deep_params')
    for module in (runner, core):
        source = Path(module.__file__).read_text()
        assert '${' not in source, f'{module.__name__} has its own interpolation'


def test_config_is_read_through_the_backend_code(tmp_path, monkeypatch):
    path = tmp_path / 'huddle.yaml'
    path.write_text(CONFIG)
    monkeypatch.setenv('HUDDLE_CONFIG', str(path))
    monkeypatch.setenv('JUDGE_KEY', 'secret')
    cfg = load_config()
    assert cfg['model'] == 'judge'
    assert cfg['models']['judge']['base_url'] == 'http://judge.test/v1'
    assert cfg['limits']['message_chars'] == 12
    model_cfg, key = config_model(cfg, 'judge')
    assert key == 'secret'
    assert model_cfg['model'] == 'judge-model'


def test_judge_setup_reads_the_config_of_the_server(tmp_path, monkeypatch):
    path = tmp_path / 'huddle.yaml'
    path.write_text(CONFIG)
    monkeypatch.setenv('HUDDLE_CONFIG', str(path))
    monkeypatch.setenv('JUDGE_KEY', 'secret')
    key, (model_cfg, api) = judge_setup()
    assert key == 'judge'                      # the active model of the config
    assert model_cfg['model'] == 'judge-model'
    assert api == 'secret'
    with pytest.raises(ValueError):
        judge_setup('missing')


def test_the_runner_defaults_to_the_active_model_of_the_config(tmp_path, monkeypatch):
    import run
    path = tmp_path / 'huddle.yaml'
    path.write_text(CONFIG)
    monkeypatch.setenv('HUDDLE_CONFIG', str(path))
    monkeypatch.delenv('HUDDLE_EVAL_BASE', raising=False)
    assert default_judge() == 'judge'
    args = run.parser().parse_args([])
    assert args.judge == 'judge'                   # the active model of the config
    assert args.base == 'http://localhost:8080'
    monkeypatch.setenv('HUDDLE_EVAL_BASE', 'http://elsewhere:9000')
    assert run.parser().parse_args([]).base == 'http://elsewhere:9000'


def test_judge_key_is_the_active_model_of_the_config():
    cfg = {'model': 'minimax', 'models': {'minimax': {}, 'openai': {}}}
    assert judge_key(cfg) == 'minimax'
    assert judge_key(cfg, 'openai') == 'openai'
    with pytest.raises(ValueError):
        judge_key({'models': {}})


def test_base_comes_from_the_environment():
    assert default_base({}) == 'http://localhost:8080'
    assert default_base({'HUDDLE_EVAL_BASE': 'http://elsewhere:9000'}) == 'http://elsewhere:9000'
    assert default_base({'HUDDLE_EVAL_BASE': '  '}) == 'http://localhost:8080'


def test_config_model_needs_a_supported_provider():
    cfg = {'models': {'judge': {'provider': 'openai_chat', 'base_url': 'http://x/v1', 'model': 'm', 'api_key_env': 'JUDGE_KEY'}}}
    with pytest.raises(ValueError):
        config_model(cfg, 'missing')
    with pytest.raises(ValueError):
        config_model({'models': {'fake': {'provider': 'fake'}}}, 'fake')


def test_the_api_key_comes_from_the_environment_or_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv('JUDGE_KEY', 'from-env')
    cfg = {'models': {'j': {'provider': 'openai_chat', 'api_key_env': 'JUDGE_KEY'}}}
    assert api_key(cfg['models']['j']) == 'from-env'
    monkeypatch.delenv('JUDGE_KEY')
    with pytest.raises(ValueError):
        api_key(cfg['models']['j'])
    token = tmp_path / 'token'
    token.write_text('  from-file\n')
    file_cfg = {'models': {'j': {'provider': 'openai_responses', 'api_key_file': str(token)}}}
    assert api_key(file_cfg['models']['j']) == 'from-file'
    with pytest.raises(ValueError):
        api_key({'models': {'j': {'provider': 'openai_responses', 'api_key_file': str(tmp_path / 'nope')}}}['models']['j'])


@pytest.mark.asyncio
async def test_judge_chat_request_shape():
    seen = {}

    def handler(request):
        seen['url'] = str(request.url)
        seen['body'] = json.loads(request.content)
        seen['headers'] = request.headers
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"verdict": true, "reason": "yes"}'}}]})

    cfg = {'provider': 'openai_chat', 'model': 'judge-model', 'base_url': 'http://judge.test/v1',
           'params': {'default': {'reasoning_effort': 'minimal'}, 'deep': {'reasoning_effort': 'low'}}}
    async with httpx.AsyncClient(base_url='http://judge.test', transport=httpx.MockTransport(handler)) as client:
        verdict = await judge_statement(client, (cfg, 'secret'), 'Every reply is in Chinese.', 'the transcript')
    assert verdict['verdict'] is True and verdict['reason'] == 'yes'
    assert seen['url'] == 'http://judge.test/v1/chat/completions'
    assert seen['headers']['authorization'] == 'Bearer secret'
    body = seen['body']
    assert body['model'] == 'judge-model'
    assert body['stream'] is False
    assert body['max_tokens'] == 1500
    assert body['reasoning_effort'] == 'low'   # the deep profile of the model
    assert body['messages'][0] == {'role': 'system', 'content': JUDGE_SYSTEM}
    assert body['messages'][1]['role'] == 'user'
    assert 'the transcript' in body['messages'][1]['content']


@pytest.mark.asyncio
async def test_judge_responses_request_shape():
    seen = {}

    def handler(request):
        seen['url'] = str(request.url)
        seen['body'] = json.loads(request.content)
        seen['headers'] = request.headers
        return httpx.Response(200, json={'output': [{'type': 'message', 'role': 'assistant',
                                                     'content': [{'type': 'output_text', 'text': '{"verdict": false, "reason": "no"}'}]}]})

    cfg = {'provider': 'openai_responses', 'model': 'judge-model', 'base_url': 'http://judge.test/v1',
           'params': {'default': {'reasoning': {'effort': 'none'}}, 'deep': {'reasoning': {'effort': 'medium'}}}}
    async with httpx.AsyncClient(base_url='http://judge.test', transport=httpx.MockTransport(handler)) as client:
        verdict = await judge_statement(client, (cfg, 'secret'), 'Every reply is in Chinese.', 'the transcript')
    assert verdict['verdict'] is False and verdict['reason'] == 'no'
    assert seen['url'] == 'http://judge.test/v1/responses'
    assert seen['headers']['authorization'] == 'Bearer secret'
    body = seen['body']
    assert body['model'] == 'judge-model'
    assert body['instructions'] == JUDGE_SYSTEM
    assert 'the transcript' in body['input']
    assert body['store'] is False
    assert body['stream'] is False
    assert body['max_output_tokens'] == 1500
    assert body['reasoning'] == {'effort': 'medium'}   # the deep profile of the model


@pytest.mark.asyncio
async def test_judge_reads_a_fenced_answer():
    def handler(request):
        return httpx.Response(200, json={'output': [{'type': 'message',
                                                     'content': [{'type': 'output_text',
                                                                  'text': '```json\n{"verdict": true, "reason": "yes"}\n```'}]}]})

    cfg = {'provider': 'openai_responses', 'model': 'judge-model', 'base_url': 'http://judge.test/v1'}
    async with httpx.AsyncClient(base_url='http://judge.test', transport=httpx.MockTransport(handler)) as client:
        verdict = await judge_statement(client, (cfg, 'secret'), 'A statement.', 'the transcript')
    assert verdict['verdict'] is True and verdict['reason'] == 'yes'


def test_message_chars_comes_from_the_config(tmp_path, monkeypatch):
    path = tmp_path / 'huddle.yaml'
    path.write_text(CONFIG)
    monkeypatch.setenv('HUDDLE_CONFIG', str(path))
    assert configured_message_chars() == 12
    assert configured_message_chars({'limits': {'message_chars': 5}}) == 5
    assert configured_message_chars({'limits': {}}) == 8000
    assert configured_message_chars({}) == 8000


def test_case_validation_uses_the_configured_limit(tmp_path, monkeypatch):
    path = tmp_path / 'huddle.yaml'
    path.write_text(CONFIG)
    monkeypatch.setenv('HUDDLE_CONFIG', str(path))
    cases = tmp_path / 'cases'
    cases.mkdir()
    (cases / 'long.yaml').write_text('- id: x\n  category: test\n  steps:\n    - say: "0123456789 abcdefghij"\n')
    with pytest.raises(ValueError):
        load_cases(cases)
    (cases / 'long.yaml').write_text('- id: x\n  category: test\n  steps:\n    - say: "short"\n')
    assert len(load_cases(cases)) == 1
