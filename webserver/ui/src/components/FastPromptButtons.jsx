import { promptStore, useFastPrompts } from '../stores/promptStore.js';
import { toast } from '../stores/toastStore.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { BusyIcon } from './Icon.jsx';

// The operator's fast prompts on the session toolbar: one button each, then a
// separator so they never read as part of the task and git actions beside
// them. Prompts are edited and added in Settings → Prompts.
//
// ``disabled`` renders the same buttons inert, for the empty header shown
// before a task is picked — so the bar does not jump when one is.
export default function FastPromptButtons({
  agentName = 'the agent',
  onSendPrompt = null,
  disabled = false,
}) {
  const prompts = useFastPrompts();
  if (prompts.length === 0) { return null; }
  return (
    <>
      {prompts.map((prompt) => (
        <FastPromptButton
          key={prompt.id}
          prompt={prompt}
          agentName={agentName}
          onSendPrompt={onSendPrompt}
          disabled={disabled}
        />
      ))}
      {/* The prompts are one group; everything after this line — search, the
          git actions, Done — is another. */}
      <span
        className="session-header-separator"
        role="separator"
        aria-orientation="vertical"
      />
    </>
  );
}

function FastPromptButton({ prompt, agentName, onSendPrompt, disabled }) {
  // Through the SAME composer send path as typing (onSendPrompt →
  // SessionDetail.onSendMessage): the prompt shows in the chat, wakes a
  // sleeping session and queues mid-turn. A raw postChatMessage skipped all of
  // that, so on a sleeping session nothing appeared. The text is read at click
  // time, so an edit in Settings applies to the next click.
  const [sending, send] = useBusyAction(
    () => (typeof onSendPrompt === 'function'
      ? onSendPrompt(promptStore.get(prompt.id))
      : Promise.resolve(false)),
    {
      onDone: (delivered) => {
        toast.show(delivered
          ? {
            kind: 'success',
            title: `${prompt.label} sent`,
            message: `Sent the prompt to ${agentName} (queued if it’s mid-turn).`,
            durationMs: 5000,
          }
          : {
            kind: 'error',
            title: `Couldn’t send ${prompt.label}`,
            message: 'The chat didn’t accept the prompt — try again.',
            durationMs: 6000,
          });
      },
    },
  );
  return (
    <button
      type="button"
      className="session-action"
      data-fast-prompt={prompt.id}
      data-tooltip={`${prompt.label} — send this prompt to ${agentName}. Edit it in Settings → Prompts.`}
      onClick={send}
      disabled={disabled || sending}
      tabIndex={disabled ? -1 : undefined}
      aria-label={sending ? `Sending ${prompt.label}…` : prompt.label}
    >
      <BusyIcon busy={sending} idle={prompt.icon} />
    </button>
  );
}
