import { createContext, useContext } from 'react';

const noop = () => {};

export const ChatComposerContext = createContext({ appendToInput: noop });

export function useChatComposer() {
  return useContext(ChatComposerContext);
}
