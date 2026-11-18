import { useEffect, useState } from 'react';
import {
  readTicketStateFilter,
  subscribeTicketStateFilter,
  writeTicketStateFilter,
} from '../utils/ticketStateFilterPref.js';

// ``[state picked ('' = all), pick]`` — live, remembered per browser.
export function useTicketStateFilter() {
  const [state, setState] = useState(() => readTicketStateFilter());
  useEffect(() => subscribeTicketStateFilter(setState), []);
  return [state, writeTicketStateFilter];
}
