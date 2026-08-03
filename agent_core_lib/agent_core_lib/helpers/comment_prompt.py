"""The single interface for describing a COMMENT to an agent.

Any surface that asks an agent to act on a comment — a pull-request review
comment, a batch of them, or an in-app diff comment — needs the same four
things, and each one exists because leaving it out caused a real, reported
failure:

    location    where the comment is       ('File: src/auth.py:42')
    code        what is actually there     (the lines, framed as untrusted)
    thread      what was already said      (self-replies removed, framed)
    guardrails  how far to go              ('smallest possible change')

Those four were previously assembled by hand in every prompt builder. The
copies drifted, and each drift produced a bug the operator saw:

  * a builder with no ``code`` turned "revert this" into a whole-file rewrite,
    because a file path and a line NUMBER leave the agent guessing.
  * a builder with no ``guardrails`` had nothing telling it to stay narrow.
  * a builder whose ``thread`` did not drop the bot's own replies fed the
    agent its previous output as if a human had written it.
  * a builder that skipped ``wrap`` pasted repo content in unframed, losing
    the prompt-injection defense.

So this module exposes ONE way to build the payload. A new comment surface
calls :func:`build_comment_prompt_context` and cannot silently omit a piece —
the value object always carries all four. Builders keep their own prose and
their own layout; only the shared payload comes from here.

Design notes:
  * ``CommentPromptContext`` is an immutable value object. It renders nothing
    by itself beyond :meth:`as_block`, so each builder stays free to place the
    parts in its own order.
  * ``CommentThreadSpec`` groups the thread inputs so the builder function
    keeps a small, single-purpose signature instead of a long parameter list.
  * ``wrap`` is injected, never imported: framing untrusted content lives in
    the sandbox library and this library depends on no other core-lib.
  * ``comment`` is duck-typed — provider review comments expose
    ``line_number``, the in-app comment store exposes ``line`` — so both work
    without an adapter at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    commented_code_block,
    comment_thread_text,
    narrow_edit_guardrails_text,
    review_comment_location_text,
)


@dataclass(frozen=True)
class CommentThreadSpec:
    """The prior turns of a comment thread, plus how to render them.

    Separate from the comment itself because the entries come from a different
    place on every surface (a provider's ``all_comments``, the local comment
    store's replies), while the RULES for rendering them must not vary.

    ``drop_prefixes`` is the important one: pass the body prefixes the calling
    product uses on replies its own bot posts, or the agent is handed its own
    previous output as though a reviewer wrote it.
    """

    entries: tuple = ()
    header: str = ''
    label_for: object = None
    drop_prefixes: tuple = field(default_factory=tuple)
    #: Provenance label the framing wrapper records for this thread. Surfaces
    #: name their own source (a pull-request thread vs an in-app one) so the
    #: agent — and anyone reading the prompt — can tell where the text came
    #: from.
    source_path: str = 'comment-thread'


@dataclass(frozen=True)
class CommentPromptContext:
    """The rendered payload describing one comment to an agent.

    Always carries all four parts (empty string where a part does not apply),
    so a builder cannot omit one by forgetting to call something.
    """

    location: str
    code: str
    thread: str
    guardrails: str

    def as_block(self) -> str:
        """The four parts in their canonical order, blank parts skipped.

        A convenience for builders with no special layout needs; builders that
        interleave their own prose read the fields directly instead.
        """
        return ''.join(
            part for part in (self.location, self.code, self.thread, self.guardrails)
            if part
        )


def build_comment_prompt_context(
    comment,
    *,
    workspace_path: str = '',
    wrap=None,
    thread: CommentThreadSpec | None = None,
    guardrail_purpose: str = 'to address this comment',
    bulleted_guardrails: bool = True,
    missing_location_label: str = '',
) -> CommentPromptContext:
    """Assemble the payload for ONE comment. The only supported entry point.

    ``wrap`` frames untrusted content (the code at the comment's line and the
    thread text). Omitting it produces an UNFRAMED payload — correct only when
    the caller frames the whole section itself, and a prompt-injection hole
    otherwise.
    """
    location = review_comment_location_text(
        comment, missing_label=missing_location_label,
    )
    code = commented_code_block(comment, workspace_path, wrap=wrap)
    spec = thread or CommentThreadSpec()
    thread_text = comment_thread_text(
        spec.entries,
        header=spec.header,
        label_for=spec.label_for,
        drop_prefixes=spec.drop_prefixes,
        wrap=wrap,
        source_path=spec.source_path,
    ) if spec.entries else ''
    return CommentPromptContext(
        location=f'{location}\n' if location else '',
        code=code,
        thread=thread_text,
        guardrails=narrow_edit_guardrails_text(
            guardrail_purpose, bulleted=bulleted_guardrails,
        ),
    )
