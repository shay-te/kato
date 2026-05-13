"""Adversarial regression test for sandbox bug:
``build_image(env=...)`` validates pins against the passed ``env``
dict but reads ``os.environ`` directly to build the actual docker
``--build-arg`` args. Mismatch → supply-chain pin bypass.

Surface (manager.py:691-755):

    def build_image(*, image_tag=..., env=None, logger=None):
        _validate_base_image_pin_or_refuse(env=env, logger=logger)
        _validate_claude_cli_version_pin_or_refuse(env=env, logger=logger)
        ...
        base_override = os.environ.get('KATO_SANDBOX_BASE_IMAGE', '').strip()  # <- WRONG
        if base_override:
            cmd.extend(['--build-arg', f'BASE_IMAGE={base_override}'])

The validators use ``env if env is not None else os.environ``, so
they correctly read from the parameter. The build-arg construction
reads ``os.environ`` directly. In a CI / test context where the
caller passes a fully-pinned ``env`` dict but ``os.environ`` doesn't
have ``KATO_SANDBOX_BASE_IMAGE`` set:

  1. Validator accepts (env has the pin).
  2. ``base_override`` is empty (os.environ is empty).
  3. Docker build proceeds with NO ``--build-arg BASE_IMAGE=...`` →
     Dockerfile falls back to the mutable floating tag (``node:22-bookworm-slim``).

Security impact: the operator gets "build accepted" with a floating
base image they thought was pinned. A hostile npm/Docker registry
during the build window could substitute the base image without
triggering any pin-mismatch error.

This test passes a fully-pinned env dict, captures the docker argv,
and verifies the build args carry the pin from the env dict.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class BugBuildImageEnvMismatchTests(unittest.TestCase):

    def test_build_image_uses_env_parameter_for_base_image_pin(self) -> None:
        # The contract: when ``env`` is passed, BOTH validation AND
        # build-arg construction must read from it. If the build-arg
        # path reads ``os.environ`` instead, a CI/test env that passes
        # pinned values to validation but not to the process env
        # silently downgrades to floating image.
        from sandbox_core_lib.sandbox_core_lib import manager

        pinned_base = 'node:22-bookworm-slim@sha256:abc1234567890abc1234567890abc1234567890abc1234567890abc1234567890'
        pinned_cli = '2.1.5'
        env = {
            'KATO_SANDBOX_BASE_IMAGE': pinned_base,
            'KATO_SANDBOX_CLAUDE_CLI_VERSION': pinned_cli,
        }

        # Capture docker invocation. Empty os.environ during the test.
        recorded_cmds = []
        def fake_run(cmd, **kwargs):
            recorded_cmds.append(list(cmd))
            return MagicMock(returncode=0, stdout='', stderr='')

        # IMPORTANT: clear os.environ of the pin vars to simulate a
        # CI context where the caller passes pinned env explicitly.
        env_to_clear = (
            'KATO_SANDBOX_BASE_IMAGE',
            'KATO_SANDBOX_CLAUDE_CLI_VERSION',
            'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE',
            'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI',
        )
        saved = {k: os.environ.pop(k, None) for k in env_to_clear}
        try:
            with patch('subprocess.run', side_effect=fake_run):
                manager.build_image(env=env)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

        # The docker build command MUST have included the env-provided
        # pin as a --build-arg. If this assertion fails, the build is
        # silently using the floating tag.
        self.assertTrue(recorded_cmds, 'docker build was never invoked')
        argv = recorded_cmds[0]
        argv_str = ' '.join(argv)
        self.assertIn(
            f'BASE_IMAGE={pinned_base}', argv_str,
            f'build_image(env=...) validated the BASE_IMAGE pin but did '
            f'NOT forward it to docker. The docker argv was:\n'
            f'  {argv_str}\n'
            f'Without --build-arg BASE_IMAGE=..., the Dockerfile uses '
            f'the floating ``node:22-bookworm-slim`` tag and supply-chain '
            f'pin protection is bypassed.',
        )
        self.assertIn(
            f'CLAUDE_CLI_VERSION={pinned_cli}', argv_str,
            f'build_image(env=...) did NOT forward CLAUDE_CLI_VERSION '
            f'to docker. CLI version pin bypassed.',
        )


if __name__ == '__main__':
    unittest.main()
