/** The side panel's styles, on the extension's tokens (client/entrypoints/content/ui.ts). */
export const panelCss = `
:host { all: initial; }
.panel {
  --ink: oklch(0.24 0.012 70); --ink-2: oklch(0.43 0.012 70); --ink-3: oklch(0.55 0.01 70);
  --paper: oklch(0.992 0.003 85); --paper-2: oklch(0.966 0.006 85); --paper-3: oklch(0.935 0.008 85);
  --line: oklch(0.9 0.008 85); --action: oklch(0.28 0.014 70); --action-ink: oklch(0.98 0.004 85);
  --focus: oklch(0.58 0.14 255); --danger: oklch(0.52 0.17 30);
  --misleading: oklch(0.55 0.19 33); --misleading-soft: oklch(0.955 0.03 35);
  --context: oklch(0.5 0.1 215); --context-soft: oklch(0.955 0.025 210);
  --controversial: oklch(0.48 0.015 260); --controversial-soft: oklch(0.945 0.006 260);
  --needs_ratings: oklch(0.52 0.1 75); --needs_ratings-soft: oklch(0.955 0.035 85);
  --tracked: oklch(0.48 0.02 260); --tracked-soft: oklch(0.95 0.005 260);
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  position: absolute; inset: 0; display: flex; flex-direction: column; box-sizing: border-box;
  background: var(--paper); color: var(--ink); border-left: 1px solid var(--line);
  font: 400 13px/1.45 var(--font); -webkit-font-smoothing: antialiased;
}
@media (prefers-color-scheme: dark) {
  .panel {
    --ink: oklch(0.94 0.006 85); --ink-2: oklch(0.79 0.01 85); --ink-3: oklch(0.66 0.01 85);
    --paper: oklch(0.235 0.008 70); --paper-2: oklch(0.275 0.009 70); --paper-3: oklch(0.31 0.01 70);
    --line: oklch(0.36 0.01 70); --action: oklch(0.93 0.008 85); --action-ink: oklch(0.22 0.01 70);
    --focus: oklch(0.72 0.13 255); --danger: oklch(0.72 0.15 30);
    --misleading: oklch(0.75 0.15 35); --misleading-soft: oklch(0.31 0.06 33);
    --context: oklch(0.76 0.09 210); --context-soft: oklch(0.3 0.04 215);
    --controversial: oklch(0.78 0.01 260); --controversial-soft: oklch(0.3 0.008 260);
    --needs_ratings: oklch(0.8 0.1 80); --needs_ratings-soft: oklch(0.31 0.04 75);
    --tracked: oklch(0.78 0.015 260); --tracked-soft: oklch(0.3 0.006 260);
  }
}
*, *::before, *::after { box-sizing: border-box; }
p { margin: 0; }
header { padding: 14px 16px 12px; border-bottom: 1px solid var(--line); }
.brand { display: flex; align-items: baseline; gap: 6px; font-size: 15px; }
.brand b { font-weight: 650; letter-spacing: -.005em; }
.brand span { color: var(--ink-3); font-size: 13px; }
.toggle {
  margin-left: auto; width: 28px; height: 28px; border: 0; border-radius: 6px; cursor: pointer;
  background: transparent; color: var(--ink-3); font: inherit; font-size: 15px;
}
.toggle:hover { background: var(--paper-2); color: var(--ink); }
form { display: flex; gap: 6px; margin-top: 10px; }
input {
  flex: 1; min-width: 0; font: 12.5px/1.4 var(--font); color: var(--ink); background: var(--paper);
  border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px;
}
input:focus { outline: none; border-color: var(--focus); box-shadow: 0 0 0 3px oklch(0.6 0.14 255 / 0.18); }
.btn {
  font: 600 12.5px/1 var(--font); height: 32px; padding: 0 14px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: var(--paper); color: var(--ink);
}
.btn.primary { background: var(--action); color: var(--action-ink); border-color: var(--action); }
.btn.primary:hover { opacity: .88; }
.status { margin-top: 10px; font-size: 12px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.status b { color: var(--ink); font-weight: 650; }
.account { margin-top: 6px; font-size: 12px; color: var(--ink-3); }
.account b { color: var(--ink-2); font-weight: 600; }
.link {
  border: 0; background: none; padding: 0; font: inherit; color: var(--ink); cursor: pointer;
  text-decoration: underline; text-underline-offset: 2px; text-decoration-color: var(--line);
}
.link:hover { text-decoration-color: currentColor; }
.err { color: var(--danger); }
.list { flex: 1; overflow: auto; padding: 10px; display: grid; align-content: start; gap: 8px; }
.item {
  display: grid; gap: 6px; justify-items: start; text-align: left; width: 100%; padding: 12px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--paper); color: var(--ink);
  font: inherit; cursor: pointer; transition: border-color 150ms, background-color 150ms;
}
.item:hover { border-color: var(--c); background: var(--c-soft); }
.chip {
  display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px 0 8px; border-radius: 999px;
  font-size: 11.5px; font-weight: 650; color: var(--c); background: var(--c-soft);
}
.chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
[data-state="misleading"] { --c: var(--misleading); --c-soft: var(--misleading-soft); }
[data-state="context"] { --c: var(--context); --c-soft: var(--context-soft); }
[data-state="controversial"] { --c: var(--controversial); --c-soft: var(--controversial-soft); }
[data-state="needs_ratings"] { --c: var(--needs_ratings); --c-soft: var(--needs_ratings-soft); }
[data-state="tracked"] { --c: var(--tracked); --c-soft: var(--tracked-soft); }
.title { font-size: 13px; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.meta { font-size: 11.5px; color: var(--ink-3); }
.empty { padding: 6px 4px; color: var(--ink-2); font-size: 12.5px; }
footer { padding: 10px 16px 12px; border-top: 1px solid var(--line); font-size: 11.5px; color: var(--ink-3); display: grid; gap: 8px; }
.demo-note { padding: 10px 12px; border-radius: 10px; background: var(--needs_ratings-soft); color: var(--ink-2); line-height: 1.5; }
.demo-note a { color: var(--ink); text-underline-offset: 2px; font-weight: 600; }

/* Loading */
.spinner {
  width: 14px; height: 14px; flex: none; border-radius: 50%;
  border: 2px solid var(--line); border-top-color: var(--ink-2); animation: spin 800ms linear infinite;
}
.skeleton {
  height: 84px; border-radius: 12px;
  background: linear-gradient(90deg, var(--paper-2) 0%, var(--paper-3) 50%, var(--paper-2) 100%);
  background-size: 200% 100%; animation: shimmer 1.3s ease-in-out infinite;
}
.skeleton:nth-child(2) { opacity: .7; } .skeleton:nth-child(3) { opacity: .45; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
@media (prefers-reduced-motion: reduce) { .spinner, .skeleton { animation: none; } }

:host(.collapsed) header > *:not(.brand), :host(.collapsed) .brand > *:not(.toggle),
:host(.collapsed) .list, :host(.collapsed) footer { display: none; }
:host(.collapsed) header { padding: 8px 6px; border: 0; }
`;
