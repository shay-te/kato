import { createHoverExpand } from './hoverExpand.js';

// The files tree while the centre is maximized: a slim rail at the left edge
// that expands over the code while it is pointed at, like the tasks menu
// (2026-11-18: "I want the tree on the left to be there BUT very very small
// width, and when I hover on it it expands"). Outside that mode it is a plain
// column and this is never consulted.
export const filesRail = createHoverExpand();
