import js from '@eslint/js';

export default [
    js.configs.recommended,
    {
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: 'module',
            globals: { process: 'readonly', console: 'readonly', structuredClone: 'readonly' },
        },
        rules: {
            'no-unused-vars': ['error', { argsIgnorePattern: '^_', ignoreRestSiblings: true }],
            eqeqeq: ['error', 'smart'],
            'prefer-const': 'error',
            'no-var': 'error',
        },
    },
];
