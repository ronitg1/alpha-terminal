# Alpha Terminal Design Notes

Alpha Terminal is an operational research terminal. Interfaces should be dense, calm, and optimized for repeated scanning.

Use the existing Tailwind tokens from `app/frontend/src/index.css`: neutral backgrounds, `border-border`, `muted-foreground`, `primary`, semantic success/destructive badges, and 6-8px radii. Keep dialogs compact, with clear labels and short helper text.

Settings surfaces use stacked sections with subtle borders, small typography, and predictable controls. Use native selects or existing shadcn-style primitives before adding dependencies.
