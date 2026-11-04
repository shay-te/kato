import { fetchEffortLevels, fetchModels } from '../api.js';
import { loadCatalog } from '../stores/catalogStore.js';
import { usePickerData } from './usePickerData.js';

// The same cached catalogues the chat composer's pickers read.
async function loadAgentChoices() {
  const [models, effort] = await Promise.all([
    loadCatalog('models', fetchModels),
    loadCatalog('levels', fetchEffortLevels),
  ]);
  return { models: models?.models || [], effort: effort || { levels: [], default: '' } };
}

// The models and effort levels a task's agent can start with:
// ``{ models: [{id, label, default}], effort: {levels, default} }`` — empty
// until they load.
export function useAgentChoices() {
  const data = usePickerData(loadAgentChoices, [], null).data;
  return { models: data?.models || [], effort: data?.effort || { levels: [], default: '' } };
}
