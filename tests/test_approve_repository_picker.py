"""Picker mechanics for ``scripts/approve_repository.py``.

Pins the per-row mode-change flow added so an operator can promote
an already-approved repo from restricted → trusted in a single
``./kato approve-repo`` pass, instead of having to revoke and
re-approve across two runs (the previous footgun).

Covered:
  * :func:`_parse_picker_input` — bare numbers / ranges become
    ``toggle`` commands, ``t<idx>`` / ``r<idx>`` become mode-set
    commands.
  * :func:`_classify_token` — head-letter sniffing.
  * :func:`_expand_index_token` — range + clamp + typo-drop.
  * :func:`_apply_picker_commands` — mutation rules on ``_Row``.
  * :func:`_resolve_row_mode` — per-row override priority.
  * :func:`_row_mark` — mode-aware display.
  * :class:`_Row` ``.changed`` / ``.effective_mode`` invariants.
"""

from __future__ import annotations

import unittest

from scripts.approve_repository import (
    _DiscoveredRepository,
    _PickerCommand,
    _Row,
    _apply_picker_commands,
    _classify_token,
    _expand_index_token,
    _parse_picker_input,
    _resolve_row_mode,
    _row_mark,
)
from kato_core_lib.data_layers.data.repository_approval import ApprovalMode


def _make_row(
    repo_id: str = 'repo-a',
    remote: str = 'https://example.com/repo-a.git',
    *,
    initially_approved: bool = False,
    initial_mode: str = '',
    initial_remote: str = '',
    selected: bool | None = None,
    pending_mode_override: str = '',
) -> _Row:
    selected_resolved = selected if selected is not None else initially_approved
    return _Row(
        repo=_DiscoveredRepository(
            repository_id=repo_id, remote_url=remote, source='checkout',
        ),
        initially_approved=initially_approved,
        initial_remote=initial_remote or (remote if initially_approved else ''),
        initial_mode=initial_mode,
        selected=selected_resolved,
        pending_mode_override=pending_mode_override,
    )


class ClassifyTokenTests(unittest.TestCase):

    def test_bare_number_is_toggle(self) -> None:
        self.assertEqual(_classify_token('26'), ('toggle', '26'))

    def test_bare_range_is_toggle(self) -> None:
        self.assertEqual(_classify_token('1-5'), ('toggle', '1-5'))

    def test_t_prefix_is_trusted(self) -> None:
        self.assertEqual(_classify_token('t26'), ('trusted', '26'))
        self.assertEqual(_classify_token('t1-5'), ('trusted', '1-5'))

    def test_r_prefix_is_restricted(self) -> None:
        self.assertEqual(_classify_token('r26'), ('restricted', '26'))
        self.assertEqual(_classify_token('r1-5'), ('restricted', '1-5'))

    def test_empty_token_falls_through_as_toggle(self) -> None:
        # The expander will reject it; classify just shrugs.
        self.assertEqual(_classify_token(''), ('toggle', ''))

    def test_unknown_letter_prefix_treated_as_toggle(self) -> None:
        # ``x99`` looks like a typo; classify keeps it as a toggle
        # candidate so the expander can drop it cleanly.
        self.assertEqual(_classify_token('x99'), ('toggle', 'x99'))

    def test_t_alone_is_toggle_token(self) -> None:
        # Bare ``t`` without a digit body is NOT a mode command —
        # the expander will reject it; we deliberately don't make
        # ``t`` apply-to-next-token because that adds stateful UX
        # complexity.
        self.assertEqual(_classify_token('t'), ('toggle', 't'))


class ExpandIndexTokenTests(unittest.TestCase):

    def test_single_index_zero_based(self) -> None:
        self.assertEqual(_expand_index_token('1', 10), [0])
        self.assertEqual(_expand_index_token('10', 10), [9])

    def test_range_inclusive(self) -> None:
        self.assertEqual(_expand_index_token('1-3', 10), [0, 1, 2])

    def test_range_clamped_to_max(self) -> None:
        self.assertEqual(_expand_index_token('1-11', 10), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_reversed_range_normalised(self) -> None:
        self.assertEqual(_expand_index_token('5-2', 10), [1, 2, 3, 4])

    def test_typo_returns_empty(self) -> None:
        self.assertEqual(_expand_index_token('abc', 10), [])
        self.assertEqual(_expand_index_token('1-abc', 10), [])
        self.assertEqual(_expand_index_token('', 10), [])

    def test_out_of_range_returns_empty(self) -> None:
        self.assertEqual(_expand_index_token('99', 10), [])
        self.assertEqual(_expand_index_token('0', 10), [])
        self.assertEqual(_expand_index_token('-5', 10), [])


class ParsePickerInputTests(unittest.TestCase):

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_parse_picker_input('', 10), [])
        self.assertEqual(_parse_picker_input('   ', 10), [])

    def test_bare_numbers_are_toggle_commands(self) -> None:
        commands = _parse_picker_input('1,3,5', 10)
        self.assertEqual(len(commands), 3)
        for cmd in commands:
            self.assertEqual(cmd.action, 'toggle')
        # Each one carries exactly its own index.
        self.assertEqual([c.indices for c in commands], [[0], [2], [4]])

    def test_bare_range_is_one_toggle_command(self) -> None:
        commands = _parse_picker_input('1-3', 10)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action, 'toggle')
        self.assertEqual(commands[0].indices, [0, 1, 2])

    def test_t_prefix_emits_trusted_command(self) -> None:
        commands = _parse_picker_input('t26', 50)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action, 'trusted')
        self.assertEqual(commands[0].indices, [25])

    def test_r_prefix_emits_restricted_command(self) -> None:
        commands = _parse_picker_input('r5', 10)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action, 'restricted')
        self.assertEqual(commands[0].indices, [4])

    def test_mixed_toggle_and_mode_in_one_line(self) -> None:
        # ``2 t5 r7-8`` — toggle row 2, set row 5 trusted, set
        # rows 7-8 restricted.
        commands = _parse_picker_input('2 t5 r7-8', 10)
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0].action, 'toggle')
        self.assertEqual(commands[0].indices, [1])
        self.assertEqual(commands[1].action, 'trusted')
        self.assertEqual(commands[1].indices, [4])
        self.assertEqual(commands[2].action, 'restricted')
        self.assertEqual(commands[2].indices, [6, 7])

    def test_t_with_range(self) -> None:
        commands = _parse_picker_input('t1-3', 10)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action, 'trusted')
        self.assertEqual(commands[0].indices, [0, 1, 2])

    def test_typo_token_dropped_silently(self) -> None:
        commands = _parse_picker_input('1, abc, t99, 3', 10)
        # ``1`` and ``3`` survive; ``abc`` parses to [], ``t99`` is
        # out of range. Two toggle commands.
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            [(c.action, c.indices) for c in commands],
            [('toggle', [0]), ('toggle', [2])],
        )

    def test_t_alone_does_not_carry_state(self) -> None:
        # ``t`` followed by a separate ``5`` does NOT mean "set 5
        # to trusted" — that stateful UX was dropped for simplicity.
        # The bare ``t`` body fails to expand → dropped.
        commands = _parse_picker_input('t 5', 10)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].action, 'toggle')
        self.assertEqual(commands[0].indices, [4])


class ApplyPickerCommandsTests(unittest.TestCase):

    def test_toggle_flips_selected(self) -> None:
        rows = [_make_row()]  # unselected
        _apply_picker_commands(rows, [_PickerCommand('toggle', [0])])
        self.assertTrue(rows[0].selected)
        _apply_picker_commands(rows, [_PickerCommand('toggle', [0])])
        self.assertFalse(rows[0].selected)

    def test_trusted_command_selects_and_overrides_mode(self) -> None:
        rows = [_make_row()]
        _apply_picker_commands(rows, [_PickerCommand('trusted', [0])])
        self.assertTrue(rows[0].selected)
        self.assertEqual(rows[0].pending_mode_override, 'trusted')

    def test_restricted_command_on_trusted_row_overrides_back(self) -> None:
        rows = [_make_row(
            initially_approved=True, initial_mode='trusted', selected=True,
        )]
        _apply_picker_commands(rows, [_PickerCommand('restricted', [0])])
        self.assertTrue(rows[0].selected)
        self.assertEqual(rows[0].pending_mode_override, 'restricted')

    def test_toggle_off_clears_pending_mode_override(self) -> None:
        # Going [t] → [ ] should reset the row — leaving a stale
        # ``pending_mode_override='trusted'`` would silently re-apply
        # trusted mode if the operator toggled back on later.
        rows = [_make_row(pending_mode_override='trusted', selected=True)]
        _apply_picker_commands(rows, [_PickerCommand('toggle', [0])])
        self.assertFalse(rows[0].selected)
        self.assertEqual(rows[0].pending_mode_override, '')

    def test_trusted_then_toggle_back_clears_override(self) -> None:
        rows = [_make_row()]
        _apply_picker_commands(rows, [
            _PickerCommand('trusted', [0]),
            _PickerCommand('toggle', [0]),
        ])
        self.assertFalse(rows[0].selected)
        self.assertEqual(rows[0].pending_mode_override, '')

    def test_commands_apply_to_all_indices_in_one_command(self) -> None:
        rows = [_make_row(repo_id=f'r-{i}') for i in range(5)]
        _apply_picker_commands(rows, [_PickerCommand('trusted', [0, 2, 4])])
        for idx in (0, 2, 4):
            self.assertTrue(rows[idx].selected)
            self.assertEqual(rows[idx].pending_mode_override, 'trusted')
        for idx in (1, 3):
            self.assertFalse(rows[idx].selected)
            self.assertEqual(rows[idx].pending_mode_override, '')


class ResolveRowModeTests(unittest.TestCase):

    def test_pending_override_wins(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            pending_mode_override='trusted',
        )
        resolved = _resolve_row_mode(row, ApprovalMode.RESTRICTED)
        self.assertEqual(resolved, ApprovalMode.TRUSTED)

    def test_re_approval_keeps_existing_mode(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='trusted', selected=True,
        )
        # The apply-time default is RESTRICTED but the row was
        # already trusted — re-approval must NOT silently demote.
        resolved = _resolve_row_mode(row, ApprovalMode.RESTRICTED)
        self.assertEqual(resolved, ApprovalMode.TRUSTED)

    def test_new_approval_uses_default(self) -> None:
        row = _make_row(selected=True)
        self.assertEqual(
            _resolve_row_mode(row, ApprovalMode.RESTRICTED),
            ApprovalMode.RESTRICTED,
        )
        self.assertEqual(
            _resolve_row_mode(row, ApprovalMode.TRUSTED),
            ApprovalMode.TRUSTED,
        )


class RowMarkTests(unittest.TestCase):

    def test_unselected_shows_empty(self) -> None:
        self.assertEqual(_row_mark(_make_row()), '[ ]')

    def test_selected_restricted_shows_r(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
        )
        self.assertEqual(_row_mark(row), '[r]')

    def test_selected_trusted_shows_t(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='trusted', selected=True,
        )
        self.assertEqual(_row_mark(row), '[t]')

    def test_pending_mode_override_changes_mark(self) -> None:
        # Was restricted, operator promoted to trusted → the
        # display shows the TARGET state (the post-apply view).
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            pending_mode_override='trusted',
        )
        self.assertEqual(_row_mark(row), '[t]')

    def test_selected_but_no_mode_known_shows_legacy_x(self) -> None:
        # Edge case: row is selected but initial_mode is blank and
        # no override. Falls back to ``[x]`` so the operator still
        # sees "this is selected" even if the mode is unknowable.
        row = _make_row(selected=True)
        self.assertEqual(_row_mark(row), '[x]')


class RowChangedTests(unittest.TestCase):

    def test_new_selection_is_a_change(self) -> None:
        self.assertTrue(_make_row(selected=True).changed)

    def test_revocation_is_a_change(self) -> None:
        row = _make_row(initially_approved=True, selected=False)
        self.assertTrue(row.changed)

    def test_unchanged_row_is_not_a_change(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
        )
        self.assertFalse(row.changed)

    def test_mode_promotion_is_a_change(self) -> None:
        # restricted → trusted via the operator setting
        # pending_mode_override. The row stays selected; the change
        # is the mode itself.
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            pending_mode_override='trusted',
        )
        self.assertTrue(row.changed)

    def test_same_mode_override_is_NOT_a_change(self) -> None:
        # Operator explicitly typed ``r26`` on a restricted-approved
        # row → no-op (same mode, same URL). Shouldn't generate a
        # spurious write.
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            pending_mode_override='restricted',
        )
        self.assertFalse(row.changed)

    def test_url_drift_is_still_a_change(self) -> None:
        # The "remote URL differs from approval" branch — kept from
        # the original logic so URL drift still triggers re-approval.
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            initial_remote='https://example.com/old.git',
            remote='https://example.com/new.git',
        )
        self.assertTrue(row.changed)


class EffectiveModeTests(unittest.TestCase):

    def test_override_wins(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
            pending_mode_override='trusted',
        )
        self.assertEqual(row.effective_mode, 'trusted')

    def test_no_override_falls_back_to_initial(self) -> None:
        row = _make_row(
            initially_approved=True, initial_mode='restricted', selected=True,
        )
        self.assertEqual(row.effective_mode, 'restricted')

    def test_unapproved_no_override_is_blank(self) -> None:
        row = _make_row()
        self.assertEqual(row.effective_mode, '')


class EndToEndPromotionTests(unittest.TestCase):
    """Smoke: simulate the one-pass restricted → trusted promotion."""

    def test_t26_on_restricted_row_promotes_in_one_pass(self) -> None:
        # 26 rows; row 26 is restricted-approved.
        rows = [_make_row(repo_id=f'r-{i}') for i in range(25)]
        rows.append(_make_row(
            repo_id='ob-love-admin-client',
            remote='https://example.com/ob-love-admin-client.git',
            initially_approved=True, initial_mode='restricted', selected=True,
        ))

        # Operator types: t26
        commands = _parse_picker_input('t26', len(rows))
        _apply_picker_commands(rows, commands)

        # Row 26 is now flagged for mode change. It IS pending
        # (changed=True) so the apply pass will rewrite it.
        target = rows[25]
        self.assertTrue(target.selected)
        self.assertEqual(target.pending_mode_override, 'trusted')
        self.assertTrue(target.changed)
        self.assertEqual(
            _resolve_row_mode(target, ApprovalMode.RESTRICTED),
            ApprovalMode.TRUSTED,
        )
        # No other row changed.
        for r in rows[:25]:
            self.assertFalse(r.changed)

    def test_t_then_toggle_off_revokes_with_no_mode_residue(self) -> None:
        # Operator first promotes, then changes their mind and
        # revokes in the same session — the row should end as a
        # clean revocation (no leftover mode override).
        rows = [_make_row(
            repo_id='r-0',
            initially_approved=True, initial_mode='restricted', selected=True,
        )]
        commands = _parse_picker_input('t1 1', len(rows))
        _apply_picker_commands(rows, commands)

        row = rows[0]
        self.assertFalse(row.selected)
        self.assertEqual(row.pending_mode_override, '')
        self.assertTrue(row.changed)  # revocation

    def test_r_on_new_row_acts_as_approve_in_restricted_mode(self) -> None:
        # ``r26`` on a previously-unapproved row is the express path
        # "approve in restricted mode" — no separate toggle needed,
        # no trusted prompt for this row.
        rows = [_make_row(repo_id='r-0')]
        commands = _parse_picker_input('r1', len(rows))
        _apply_picker_commands(rows, commands)

        row = rows[0]
        self.assertTrue(row.selected)
        self.assertEqual(row.pending_mode_override, 'restricted')
        self.assertTrue(row.changed)
        self.assertEqual(
            _resolve_row_mode(row, ApprovalMode.TRUSTED),
            # Override beats the apply-time default.
            ApprovalMode.RESTRICTED,
        )

    def test_mixed_batch_routes_each_row_correctly(self) -> None:
        # rows: 1 new unapproved, 1 restricted-approved, 1 trusted-approved.
        # Operator types: ``1 t2 r3`` — approve row 1 in default mode,
        # promote row 2 to trusted, demote row 3 to restricted.
        rows = [
            _make_row(repo_id='r-0'),
            _make_row(
                repo_id='r-1', initially_approved=True,
                initial_mode='restricted', selected=True,
            ),
            _make_row(
                repo_id='r-2', initially_approved=True,
                initial_mode='trusted', selected=True,
            ),
        ]
        commands = _parse_picker_input('1 t2 r3', len(rows))
        _apply_picker_commands(rows, commands)

        # Row 1: toggled on, no override → uses default mode at apply.
        self.assertTrue(rows[0].selected)
        self.assertEqual(rows[0].pending_mode_override, '')
        self.assertTrue(rows[0].changed)

        # Row 2: trusted override on a restricted approval → changed.
        self.assertEqual(rows[1].pending_mode_override, 'trusted')
        self.assertTrue(rows[1].changed)
        self.assertEqual(
            _resolve_row_mode(rows[1], ApprovalMode.RESTRICTED),
            ApprovalMode.TRUSTED,
        )

        # Row 3: restricted override on a trusted approval → changed.
        self.assertEqual(rows[2].pending_mode_override, 'restricted')
        self.assertTrue(rows[2].changed)
        self.assertEqual(
            _resolve_row_mode(rows[2], ApprovalMode.TRUSTED),
            ApprovalMode.RESTRICTED,
        )


if __name__ == '__main__':
    unittest.main()
