import js from '@eslint/js';
import globals from 'globals';

/**
 * Flat ESLint config for static/*.js and static/modules/*.js.
 *
 * The frontend is plain (non-module) scripts that share the global lexical
 * scope — app.js reads state that track-map.js writes and vice versa. That
 * makes `no-undef` impractical without maintaining a long manual globals
 * list, so it's disabled here. Re-enable + enumerate if cross-script refs
 * stabilise and we want typo protection.
 */
export default [
  js.configs.recommended,
  {
    files: ['static/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: {
        ...globals.browser,
        Plotly: 'readonly',
      },
    },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
];
