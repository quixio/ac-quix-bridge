import globals from "globals";

const baseRules = {
  "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
  "no-undef": "error",
  "no-var": "error",
  "prefer-const": "warn",
  eqeqeq: ["error", "always", { null: "ignore" }],
};

export default [
  {
    files: ["static/**/*.js"],
    ignores: ["static/**/*.test.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser },
    },
    rules: baseRules,
  },
  {
    files: ["static/**/*.test.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
    },
    rules: baseRules,
  },
];
