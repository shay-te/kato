import { useEffect } from 'react';
import { fetchModels, fetchReviewLoopDefaultModel } from '../api.js';
import { loadCatalog } from '../stores/catalogStore.js';
import { usePickerData } from './usePickerData.js';
import { reviewerModelChoice } from '../components/reviewLoop/reviewLoopHelpers.js';
import {
  useReviewLoopModel,
  writeReviewLoopModel,
} from '../components/reviewLoop/reviewLoopModelPref.js';

// The models kato offers (the chat's own cached catalogue) and the reviewer's
// default — fetched once and shared by the picker and the running loop's name.
async function loadReviewerModels() {
  const [catalog, defaultModel] = await Promise.all([
    loadCatalog('models', fetchModels),
    loadCatalog('reviewLoopDefaultModel', fetchReviewLoopDefaultModel),
  ]);
  return { models: catalog?.models || [], defaultModel };
}

// ``reviewerModelChoice`` for the operator's remembered pick. A pick that is
// no longer offered is forgotten, so Start never sends a model the picker
// isn't showing.
export function useReviewerModelChoice() {
  const picked = useReviewLoopModel();
  const { data } = usePickerData(loadReviewerModels, [], null);
  const choice = reviewerModelChoice({
    models: data?.models, defaultModel: data?.defaultModel, picked,
  });
  useEffect(() => {
    if (choice.stale) { writeReviewLoopModel(''); }
  }, [choice.stale]);
  return choice;
}
