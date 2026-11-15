"""Quick start: a first run that asks only what kato cannot find out itself.

The first-run wizard used to open with six steps, the first of them a ticket
system's address, token, project and assignee — before kato had done anything.
Quick start needs three settings, and finds all three:

* the agent: Claude Code (``KATO_AGENT_BACKEND=claude``) — kato checks that it
  is installed and signed in, installs it with Anthropic's own installer, and
  runs its sign-in (``agent_check``, ``claude_sign_in``);
* the code: the folder holding the most repositories (``code_folders``);
* the ticket system: none (``KATO_ISSUE_PLATFORM=local``) — tasks are written in
  kato; a tracker is connected later, when wanted.

``git_check`` makes sure ``git`` is really there (a Mac without Apple's command
line tools has a ``git`` that only offers to install itself). ``settings``
saves the three in one write. One job per module.
"""
