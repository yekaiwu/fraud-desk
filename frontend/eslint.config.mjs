import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Untrusted text (evidence, names, reasons) is rendered as React text only (frontend.md section 3).
    rules: {
      "react/no-danger": "error",
    },
  },
  globalIgnores([".next/**", ".next-*/**", "out/**", "build/**", "coverage/**", "next-env.d.ts"]),
]);

export default eslintConfig;
