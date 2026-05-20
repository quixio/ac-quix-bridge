import js from '@eslint/js';
import globals from 'globals';

/**
 * Flat ESLint config for static/*.js and static/modules/*.js.
 *
 * Two file groups:
 *   1. Classic scripts (sourceType: 'script'): toast.js, track-map.js,
 *      video-overlay.js — share the global lexical scope with one another
 *      and with the module graph via `window.*`.
 *   2. ES modules (sourceType: 'module'): app.js + modules/state.js,
 *      data.js, selections.js, sync.js, charts.js, video.js — loaded via
 *      <script type="module"> in index.html.
 *
 * `no-undef` stays off because the module↔classic boundary is bridged via
 * implicit globals (trackData, trackConfig, markerPosition, trackZoom,
 * trackBaseRange, window.renderTrackMap, window.trackPointAtNorm,
 * window.applyZoom, window.showToast) which lint would otherwise flag.
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
  {
    files: [
      'static/app.js',
      'static/modules/state.js',
      'static/modules/data.js',
      'static/modules/selections.js',
      'static/modules/sync.js',
      'static/modules/charts.js',
      'static/modules/video.js',
      'static/modules/auth.js',
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
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
  {
    files: ['static/modules/*.test.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
];
