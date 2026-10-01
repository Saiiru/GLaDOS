import asyncio
import subprocess
from pathlib import Path


def test_skill_library_searches_metadata_and_reads_only_named_skill(tmp_path):
    from skill_library import SkillLibrary
    skill = tmp_path / 'research' / 'market-scan' / 'SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('''---
name: market-scan
description: Research competitors and market news.
---
# Workflow
Check sources and cite them.
''')
    library = SkillLibrary(root=tmp_path)
    matches = library.search('competitors news')
    assert matches[0]['name'] == 'market-scan'
    assert 'Research competitors' in matches[0]['description']
    assert 'cite them' in library.read('market-scan')
    try:
        library.read('../secret')
    except ValueError:
        pass
    else:
        raise AssertionError('Skill reads must reject traversal')


def test_skill_read_resolves_unambiguous_guide_suffix_from_qwen_tool_call(tmp_path):
    from skill_library import SkillLibrary
    skill = tmp_path / 'test-driven-development' / 'SKILL.md'
    skill.parent.mkdir()
    skill.write_text('''---
name: test-driven-development
description: Use TDD.
---
Core principle: watch the test fail first.''')

    content = SkillLibrary(root=tmp_path).read('test-driven-development-guide')

    assert 'watch the test fail first' in content


def test_skill_search_ranks_direct_title_and_topic_match_above_incidental_mentions(tmp_path):
    from skill_library import SkillLibrary
    for name, description in [('google-workspace', 'Use Python for spreadsheets.'),
                              ('python-testing', 'Run automated test suites.')]:
        path = tmp_path / name / 'SKILL.md'
        path.parent.mkdir()
        path.write_text(f'''---
name: {name}
description: {description}
---
Guide.''')
    matches = SkillLibrary(root=tmp_path).search('python tests')
    assert matches[0]['name'] == 'python-testing'


def test_skill_search_includes_guidance_for_the_top_match(tmp_path):
    from skill_library import SkillLibrary
    skill = tmp_path / 'audio-routing' / 'SKILL.md'
    skill.parent.mkdir()
    skill.write_text('''---
name: audio-routing-troubleshooting
description: Resolve Linux audio device routing conflicts.
---
## Procedure
1. First inspect PipeWire defaults with `wpctl status`. Do not change routing until identified.
2. Only then consider a reversible adjustment.''')
    result = SkillLibrary(root=tmp_path).search_with_guidance('Linux audio routing')
    assert result[0]['name'] == 'audio-routing-troubleshooting'
    assert 'wpctl status' in result[0]['guidance']
    assert 'wpctl status' in result[0]['first_step']


def test_skill_search_normalizes_common_portuguese_audio_terms(tmp_path):
    from skill_library import SkillLibrary
    skill = tmp_path / 'audio-routing' / 'SKILL.md'
    skill.parent.mkdir()
    skill.write_text('''---
name: audio-routing-troubleshooting
description: Use for Linux audio devices with routing conflicts.
---
Diagnose audio devices and routing problems on Linux.''')
    matches = SkillLibrary(root=tmp_path).search('áudio com dispositivos conflitantes no Linux')
    assert matches and matches[0]['name'] == 'audio-routing-troubleshooting'


def test_codex_agent_uses_ephemeral_workspace_sandbox_and_returns_final_message(tmp_path):
    from codex_agent import CodexAgent
    from skill_library import SkillLibrary
    root = tmp_path / 'project'
    root.mkdir()
    skill_root = tmp_path / 'skills'
    skill_file = skill_root / 'test-driven-development' / 'SKILL.md'
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text('''---
name: test-driven-development
description: Use TDD.
---
Write failing tests first.''')
    library = SkillLibrary(root=skill_root)
    seen = {}
    def runner(args, **kwargs):
        seen['args'] = args
        seen['kwargs'] = kwargs
        output = Path(args[args.index('--output-last-message') + 1])
        output.write_text('Implemented and tested the requested change.')
        return subprocess.CompletedProcess(args, 0, stdout='{}\n', stderr='')
    agent = CodexAgent(executable='/usr/bin/codex', runner=runner)
    result = asyncio.run(agent.run('Add a small feature', root,
        skills=['test-driven-development'], skill_library=library))
    assert result == 'Implemented and tested the requested change.'
    args = seen['args']
    assert args[:3] == ['/usr/bin/codex', 'exec', '--sandbox']
    assert args[3] == 'workspace-write'
    assert '--ephemeral' in args
    assert '--skip-git-repo-check' in args
    assert '--json' in args
    assert str(root) == args[args.index('--cd') + 1]
    prompt = seen['kwargs']['input']
    assert 'USER-APPROVED CODING TASK:\nAdd a small feature' in prompt
    assert 'Do not commit, push, publish' in prompt
    assert 'Write failing tests first.' in prompt
    assert '[BEGIN SKILL GUIDANCE: test-driven-development]' in prompt
    assert seen['kwargs']['shell'] is False
    assert seen['kwargs']['timeout'] <= 900


def test_agent_tool_registry_includes_codex_and_skill_library():
    from tools import tools_list
    names = {tool['name'] for tool in tools_list[0]['function_declarations']}
    assert {'run_codex_task', 'search_skills', 'read_skill'} <= names


def test_glados_inspired_system_prompt_keeps_safety_and_natural_ptbr():
    from local_session import LocalSession
    prompt = LocalSession(None, [], None, None).messages[0]['content'].casefold()
    assert 'português brasileiro' in prompt
    assert 'humor clínico, seco' in prompt
    assert 'útil primeiro' in prompt
    assert 'sem crueldade' in prompt
    assert 'search_skills' in prompt and 'read_skill' in prompt
    assert 'confirmação da tarefa exata' in prompt


def test_codex_agent_adds_task_relevant_skill_if_qwen_selects_an_unrelated_one(tmp_path):
    from codex_agent import CodexAgent
    from skill_library import SkillLibrary
    root = tmp_path / 'project'; root.mkdir()
    skill_root = tmp_path / 'skills'
    entries = [
        ('test-driven-development', 'TDD: tests before code.', 'Write failing tests first.'),
        ('claude-code', 'Delegate coding to Claude Code.', 'Use Claude for implementation.'),
    ]
    for name, description, body in entries:
        skill = skill_root / name / 'SKILL.md'; skill.parent.mkdir(parents=True)
        skill.write_text(f'''---
name: {name}
description: {description}
---
{body}''')
    seen = {}
    def runner(args, **kwargs):
        seen['prompt'] = kwargs['input']
        Path(args[args.index('--output-last-message') + 1]).write_text('Done.')
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    asyncio.run(CodexAgent('/usr/bin/codex', runner=runner).run(
        'Write a unit test for a helper', root, skills=['claude-code'], skill_library=SkillLibrary(skill_root)))
    assert '[BEGIN SKILL GUIDANCE: test-driven-development]' in seen['prompt']


def test_skill_search_prefers_tdd_for_unit_testing_queries(tmp_path):
    from skill_library import SkillLibrary
    entries = [
        ('claude-code', 'Delegate coding workflows.', 'Use unit test loops.'),
        ('test-driven-development', 'TDD: enforce RED-GREEN-REFACTOR.', 'Write tests first. Verify they fail before implementation.'),
    ]
    for name, description, body in entries:
        skill = tmp_path / name / 'SKILL.md'
        skill.parent.mkdir()
        skill.write_text(f'''---
name: {name}
description: {description}
---
{body}''')
    matches = SkillLibrary(root=tmp_path).search('unit testing')
    assert matches and matches[0]['name'] == 'test-driven-development'


def test_skill_search_maps_portuguese_email_and_home_assistant_topics(tmp_path):
    from skill_library import SkillLibrary
    entries = [
        ('email-inbox-triage', 'Triage inbox email threads.'),
        ('home-assistant', 'Control a smart home assistant.'),
    ]
    for name, description in entries:
        skill = tmp_path / name / 'SKILL.md'
        skill.parent.mkdir()
        skill.write_text(f'''---
name: {name}
description: {description}
---
Workflow.''')
    library = SkillLibrary(root=tmp_path)
    assert library.search('e-mail caixa de entrada')[0]['name'] == 'email-inbox-triage'
    assert library.search('assistente de casa')[0]['name'] == 'home-assistant'
