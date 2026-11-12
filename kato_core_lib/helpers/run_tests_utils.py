"""What the agent is told when the operator turned "Run tests" off.

Kato's own testing step is skipped at its gate (``AgentService``). That alone
would leave the agent running the suite anyway — the review loop's fix
messages ask for it, and agents run tests by habit — so every chat kato
starts carries this in its system prompt, after kato's own guidance. Read at
the spawn: a chat already running keeps the instructions it started with.

It says it overrides later instructions on purpose: the review loop's fixer
rules ("run the tests that cover it") reach the chat as a message, and the
operator's switch has to win over them.
"""

from __future__ import annotations

from kato_core_lib.helpers.automation_switches_utils import run_tests_enabled

NO_TESTS_GUIDANCE = '\n'.join([
    '## Tests are off for this task',
    'The operator turned automatic test runs off. Do not run the test suite, '
    'linters or type checks, and do not start a test run to check your work — '
    'unless the operator asks for one in this chat. Writing or updating tests '
    'is still fine when the task calls for it. When you report, say what you '
    'did not verify by running it. This overrides any other instruction to '
    'run tests, including the review loop\'s.',
])


def with_test_policy(guidance: str, env: dict | None = None) -> str:
    """``guidance`` plus the no-tests section when "Run tests" is off."""
    if run_tests_enabled(env):
        return guidance
    base = str(guidance or '').rstrip()
    return f'{base}\n\n{NO_TESTS_GUIDANCE}' if base else NO_TESTS_GUIDANCE
