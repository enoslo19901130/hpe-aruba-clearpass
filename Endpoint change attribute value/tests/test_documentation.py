"""持續檢查所有 Python 模組、類別與函式皆包含功能說明。"""

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORIES = {".venv", "__pycache__"}


class DocumentationTests(unittest.TestCase):
    """防止後續修改加入未註解的 Python 功能單位。"""

    def test_all_python_definitions_have_docstrings(self):
        """掃描專案 Python AST，列出缺少模組或定義 docstring 的位置。"""
        missing = []

        for path in sorted(PROJECT_ROOT.rglob("*.py")):
            relative_path = path.relative_to(PROJECT_ROOT)
            if any(part in IGNORED_DIRECTORIES for part in relative_path.parts):
                continue

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if not ast.get_docstring(tree):
                missing.append(f"{relative_path}:1 module")

            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                if not ast.get_docstring(node):
                    missing.append(
                        f"{relative_path}:{node.lineno} {node.__class__.__name__} "
                        f"{node.name}"
                    )

        self.assertFalse(
            missing,
            "以下 Python 功能缺少 docstring：\n" + "\n".join(missing),
        )


if __name__ == "__main__":
    # 支援直接執行單一測試檔，也可由 unittest discover 統一載入。
    unittest.main()
