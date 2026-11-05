// "Open task X's definition" — its title and description — as a tab among its
// files (from the header's task title). App acts on it (useOpenRequests).
import { createOpenRequests } from '../../stores/pubsub.js';

export const taskDefinitionView = createOpenRequests();
