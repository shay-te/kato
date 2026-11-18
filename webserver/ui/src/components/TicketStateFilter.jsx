import { hasTicketStates, ticketStateOptions } from '../utils/ticketStateFilter.js';

// The tasks menu's status dropdown: "All", or one state of the tasks' tickets
// (YouTrack / Jira / local), each with how many tasks are in it. Nothing to
// show until some task's state is known.
export default function TicketStateFilter({ sessions, value, onChange }) {
  if (!hasTicketStates(sessions, value)) { return null; }
  return (
    <select
      className="pill-select tabs-state-filter"
      aria-label="Show the tasks in one status"
      title="Show only the tasks whose ticket is in one status (YouTrack / Jira), or all of them"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {ticketStateOptions(sessions, value).map((option) => (
        <option key={option.value || '__all__'} value={option.value}>
          {`${option.label} (${option.count})`}
        </option>
      ))}
    </select>
  );
}
