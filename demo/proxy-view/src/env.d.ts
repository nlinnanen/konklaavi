// The extension's modules read WXT's import.meta.env; build.mjs defines DEV as false.
interface ImportMeta {
  readonly env: { readonly DEV: boolean };
}
