import { defineConfig, type Plugin } from "vite";

/**
 * Workaround for upstream BlockSuite packaging issues when building outside
 * the AFFiNE monorepo:
 *
 * 1. Icon typo: affine-components imports "CheckBoxCkeckSolidIcon" (misspelled)
 *    but @blocksuite/icons exports "CheckBoxCheckSolidIcon".
 * 2. CJS interop: simple-xml-to-json's .mjs has only named exports but
 *    BlockSuite import-mindmap.js uses a default import.
 */
function fixBlockSuiteCompat(): Plugin {
  return {
    name: "fix-blocksuite-compat",
    transform(code, id) {
      let modified = false;
      let result = code;

      // Fix icon typo
      if (id.includes("@blocksuite") && code.includes("CheckBoxCkeckSolidIcon")) {
        result = result.replace(/CheckBoxCkeckSolidIcon/g, "CheckBoxCheckSolidIcon");
        modified = true;
      }

      // Fix simple-xml-to-json default import — rewrite to namespace import
      if (id.includes("import-mindmap") && code.includes("import c from 'simple-xml-to-json'")) {
        result = result.replace(
          "import c from 'simple-xml-to-json'",
          "import * as c from 'simple-xml-to-json'",
        );
        modified = true;
      }

      return modified ? result : undefined;
    },
  };
}

export default defineConfig({
  plugins: [fixBlockSuiteCompat()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:7380",
      "/ws": {
        target: "ws://127.0.0.1:7380",
        ws: true,
      },
      "/health": "http://127.0.0.1:7380",
    },
  },
});
