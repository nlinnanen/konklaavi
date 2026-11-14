/** The top bar's styles, on the extension's tokens (client/entrypoints/content/ui.ts). */
export const barCss = `
:host { all: initial; }
.bar {
  --ink: oklch(0.24 0.012 70); --ink-2: oklch(0.43 0.012 70); --ink-3: oklch(0.55 0.01 70);
  --paper: oklch(0.992 0.003 85); --paper-2: oklch(0.966 0.006 85); --line: oklch(0.9 0.008 85);
  --action: oklch(0.28 0.014 70); --action-ink: oklch(0.98 0.004 85); --focus: oklch(0.58 0.14 255);
  --danger: oklch(0.52 0.17 30);
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  position: absolute; inset: 0; display: flex; align-items: center; gap: 12px; padding: 0 12px 0 14px;
  box-sizing: border-box; background: var(--paper-2); color: var(--ink-2); border-bottom: 1px solid var(--line);
  font: 400 13px/1.3 var(--font); -webkit-font-smoothing: antialiased; white-space: nowrap;
}
@media (prefers-color-scheme: dark) {
  .bar {
    --ink: oklch(0.94 0.006 85); --ink-2: oklch(0.79 0.01 85); --ink-3: oklch(0.66 0.01 85);
    --paper: oklch(0.235 0.008 70); --paper-2: oklch(0.275 0.009 70); --line: oklch(0.36 0.01 70);
    --action: oklch(0.93 0.008 85); --action-ink: oklch(0.22 0.01 70); --danger: oklch(0.72 0.15 30);
  }
}
*, *::before, *::after { box-sizing: border-box; }
.badge {
  flex: none; font-size: 11px; font-weight: 700; letter-spacing: .08em; padding: 4px 8px; border-radius: 6px;
  background: var(--ink); color: var(--paper); cursor: help;
}
form { flex: 1 1 420px; max-width: 640px; min-width: 160px; display: flex; gap: 6px; }
input {
  flex: 1; min-width: 0; height: 32px; font: 13px/1 var(--font); color: var(--ink); background: var(--paper);
  border: 1px solid var(--line); border-radius: 8px; padding: 0 10px;
}
input:focus { outline: none; border-color: var(--focus); box-shadow: 0 0 0 3px oklch(0.6 0.14 255 / 0.18); }
.btn {
  flex: none; font: 600 12.5px/1 var(--font); height: 32px; padding: 0 14px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: var(--paper); color: var(--ink);
}
.btn.primary { background: var(--action); color: var(--action-ink); border-color: var(--action); }
.btn.primary:hover { opacity: .88; }
.btn.quiet { border-color: transparent; background: transparent; color: var(--ink-2); padding: 0 8px; }
.btn.quiet:hover { color: var(--ink); background: var(--paper); }
.btn:focus-visible, a:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.status { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; display: flex; align-items: center; gap: 8px; }
.status b { color: var(--ink); font-weight: 650; }
.err { color: var(--danger); }
.readme { flex: none; margin-left: auto; color: var(--ink); font-weight: 600; text-underline-offset: 2px; }
.spinner {
  width: 14px; height: 14px; flex: none; border-radius: 50%;
  border: 2px solid var(--line); border-top-color: var(--ink-2); animation: spin 800ms linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
@media (max-width: 760px) { .status, [data-act=guide] { display: none; } }
`;

/** The guided tour over the page, on the same tokens. */
export const guideCss = `
:host { all: initial; }
.dim, .hole, .card {
  --ink: oklch(0.24 0.012 70); --ink-2: oklch(0.43 0.012 70); --ink-3: oklch(0.55 0.01 70);
  --paper: oklch(0.992 0.003 85); --paper-2: oklch(0.966 0.006 85); --line: oklch(0.9 0.008 85);
  --action: oklch(0.28 0.014 70); --action-ink: oklch(0.98 0.004 85); --focus: oklch(0.58 0.14 255);
  --scrim: oklch(0.2 0.01 70 / 0.5);
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  .dim, .hole, .card {
    --ink: oklch(0.94 0.006 85); --ink-2: oklch(0.79 0.01 85); --ink-3: oklch(0.66 0.01 85);
    --paper: oklch(0.235 0.008 70); --paper-2: oklch(0.275 0.009 70); --line: oklch(0.36 0.01 70);
    --action: oklch(0.93 0.008 85); --action-ink: oklch(0.22 0.01 70); --scrim: oklch(0 0 0 / 0.6);
  }
}
*, *::before, *::after { box-sizing: border-box; }
.hidden { display: none !important; }
.dim { position: fixed; inset: 0; background: var(--scrim); animation: fade 180ms ease-out; }
/* The spotlight: a hole whose shadow dims everything around it. */
.hole {
  position: fixed; border-radius: 10px; pointer-events: none;
  box-shadow: 0 0 0 2px oklch(1 0 0 / 0.9), 0 0 0 100vmax var(--scrim);
  transition: left 260ms cubic-bezier(0.25, 1, 0.5, 1), top 260ms cubic-bezier(0.25, 1, 0.5, 1),
    width 260ms cubic-bezier(0.25, 1, 0.5, 1), height 260ms cubic-bezier(0.25, 1, 0.5, 1);
}
.card {
  position: fixed; width: min(400px, calc(100vw - 32px)); padding: 18px 20px 16px;
  background: var(--paper); color: var(--ink); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 1px 2px oklch(0.2 0.01 70 / 0.08), 0 24px 60px -12px oklch(0.2 0.01 70 / 0.45);
  font: 400 14px/1.5 var(--font); -webkit-font-smoothing: antialiased;
  transition: left 260ms cubic-bezier(0.25, 1, 0.5, 1), top 260ms cubic-bezier(0.25, 1, 0.5, 1);
}
.eyebrow { margin: 0; font-size: 11.5px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
h2 { margin: 2px 0 8px; font-size: 17px; font-weight: 650; letter-spacing: -.01em; }
.body { color: var(--ink-2); }
.body b { color: var(--ink); font-weight: 650; }
.body a { color: var(--ink); font-weight: 600; text-underline-offset: 2px; }
.swatches { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.swatch { font-size: 12.5px; padding: 1px 4px; border-radius: 3px; color: var(--ink); }
.actions { display: flex; align-items: center; gap: 8px; margin-top: 16px; }
.dots { display: flex; gap: 5px; margin: 0 auto; }
.dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--line); }
.dots i.on { background: var(--ink-2); }
.btn {
  font: 600 13px/1 var(--font); height: 34px; padding: 0 14px; border-radius: 9px; cursor: pointer;
  border: 1px solid var(--line); background: var(--paper); color: var(--ink);
}
.btn:hover { background: var(--paper-2); }
.btn.primary { border-color: var(--action); background: var(--action); color: var(--action-ink); }
.btn.primary:hover { opacity: .88; }
.btn.quiet { border-color: transparent; background: transparent; color: var(--ink-3); padding: 0 6px; }
.btn.quiet:hover { color: var(--ink); }
.btn[hidden] { display: none; }
.btn:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
@keyframes fade { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .dim { animation: none; } .hole, .card { transition: none; } }
`;
