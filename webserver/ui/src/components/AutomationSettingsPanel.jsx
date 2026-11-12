import SchemaFieldGroup from './settings/SchemaFieldGroup.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsSectionHead from './settings/SettingsSectionHead.jsx';

// "Automation" tab — every switch for what kato does ON ITS OWN in a task, in
// the order a task meets them. The switches are schema fields tagged
// ``panel: 'automation'`` (lifted out of General, Security scanner, Release
// notes and Email), so each keeps its label, help and default in the schema;
// their sections keep the details (which scanners, which file, which address).
//
// One group per heading: a group saves only its own changed keys, so a tab
// that splits one section across several headings never clobbers the rest.

const PHASES = [
  {
    title: 'Before the agent starts',
    help: 'Which scanners run is set in Security scanner.',
    groups: [['security_scanner', ['KATO_SECURITY_SCANNER_ENABLED']]],
  },
  {
    title: 'While the agent works',
    help: 'Whether the agent may also commit on its own branch is its own tab: Strict git.',
    groups: [['general', ['KATO_RUN_TESTS']]],
  },
  {
    title: 'When the work is finished',
    help: 'The Push and Done buttons are yours and always work, whatever is switched here.',
    groups: [
      ['general', ['KATO_AUTO_PUSH_ENABLED', 'KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS', 'KATO_STOP_CONTAINERS_ON_DONE']],
      ['release_notes', ['KATO_RELEASE_NOTES_ENABLED']],
      ['email_slack', ['KATO_COMPLETION_EMAIL_ENABLED', 'KATO_FAILURE_EMAIL_ENABLED']],
    ],
  },
  {
    title: 'Comments it answers by itself',
    help: 'Ticket comments and pull-request review comments kato picks up and works on.',
    groups: [['general', [
      'KATO_TASK_COMMENTS_ENABLED', 'KATO_TASK_COMMENTS_REQUIRE_MENTION',
      'KATO_REVIEW_COMMENTS_ENABLED', 'KATO_REVIEW_COMMENTS_REQUIRE_MENTION',
    ]]],
  },
];

export default function AutomationSettingsPanel({ highlightKey = '' }) {
  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Automation">
        <p>
          What kato does on its own while it works on a task. Switch off
          anything you would rather do yourself.
        </p>
      </SettingsPanelHead>
      {PHASES.map((phase) => (
        <div key={phase.title}>
          <SettingsSectionHead title={phase.title}>
            <p className="settings-drawer-field-group-help">{phase.help}</p>
          </SettingsSectionHead>
          {phase.groups.map(([sectionId, keys]) => (
            <SchemaFieldGroup
              key={`${sectionId}:${keys[0]}`}
              sectionId={sectionId}
              panel="automation"
              keys={keys}
              highlightKey={highlightKey}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
