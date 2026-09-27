import js from "@eslint/js";
import globals from "globals";
import { defineConfig } from "eslint/config";

export default defineConfig([
  {
    files: ["**/*.{js,mjs,cjs}"],
    plugins: { js },
    extends: ["js/recommended"],

    languageOptions: {
      globals: globals.browser,
    },

    linterOptions: {
      noInlineConfig: true,
    },

    rules: {
      "max-len": ["error", { code: 100, tabWidth: 2, ignoreUrls: true }],
      "indent": ["error", 2, { SwitchCase: 1 }],
      "semi": ["error", "always"],
      "quotes": ["error", "double", { avoidEscape: true }],
      "no-trailing-spaces": "error",
      "eol-last": ["error", "always"],
      "object-curly-spacing": ["error", "always"],
      "array-bracket-spacing": ["error", "never"],
      "space-before-blocks": ["error", "always"],
      "keyword-spacing": ["error", { before: true, after: true }],
      "arrow-spacing": ["error", { before: true, after: true }],
      "arrow-parens": ["error", "always"],
      "comma-dangle": ["error", "always-multiline"],
      "comma-spacing": ["error", { before: false, after: true }],
    },
  },
]);
