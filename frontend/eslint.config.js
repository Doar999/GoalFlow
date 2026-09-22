import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // 生成物不参与 lint，它由 scripts/api-generate.sh 产生。
    ignores: ["dist", "src/shared/api/generated"],
  },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // any、非空断言、ts-ignore 必须在同行写明理由，
      // 见 docs/engineering/03-code-and-test-standards.md 第 3 节。
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "src/shared/test/**"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
);
