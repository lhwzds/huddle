from pathlib import Path
import pytest
from huddle.config import load_config, load_agents, params_for

def test_default_path_and_agents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HUDDLE_MODEL', 'fake')
    cfg = load_config()
    assert cfg['model'] == 'fake'
    agents = load_agents(cfg)
    assert [a.key for a in agents] == ['mira', 'theo', 'juno']
    assert all(a.persona and a.knowledge for a in agents)

def test_interpolation_and_agents_override(monkeypatch, tmp_path):
    group = tmp_path / 'groups' / 'crew'
    (group / 'agents').mkdir(parents=True)
    (group / 'group.md').write_text('---\nname: Crew\ndescription: test\nmembers: [sam]\n---\nWe share this.\n')
    (group / 'agents' / 'sam.md').write_text('---\nname: Sam\nrole: ${ROLE}\ncolor: red\nlens: focus\nmodel: fake\n---\n## Persona\nPerson\n## Private knowledge\nSecret\n')
    config = tmp_path / 'huddle.yaml'
    config.write_text('groups: [crew]\nname: ${ROLE}\nitems: ["${UNSET:-fallback}", "${ROLE}"]\n')
    monkeypatch.setenv('HUDDLE_CONFIG', str(config))
    monkeypatch.setenv('ROLE', 'test')
    cfg = load_config()
    assert cfg['items'] == ['fallback', 'test']
    agent = load_agents(cfg)[0]
    assert (agent.role, agent.model, agent.persona, agent.knowledge, agent.common) == ('test', 'fake', 'Person', 'Secret', 'We share this.')
    config.write_text('name: ${MISSING_VAR}\n')
    with pytest.raises(ValueError, match='MISSING_VAR'):
        load_config()

def test_deep_merge_no_mutation():
    cfg = {'params': {'default': {'nested': {'a': 1, 'b': 2}, 'x': [1]}, 'deep': {'nested': {'b': 3, 'c': 4}}}}
    result = params_for(cfg, 'deep')
    assert result == {'nested': {'a': 1, 'b': 3, 'c': 4}, 'x': [1]}
    result['x'].append(2)
    assert cfg['params']['default']['x'] == [1]
