import globals from "globals";

export default [
  {
    files: ["static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-undef": "error",
      "no-var": "error",
      "prefer-const": "warn",
      eqeqeq: ["error", "always", { null: "ignore" }],
    },
  },
];
