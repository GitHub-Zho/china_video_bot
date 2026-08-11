#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
cd "$REPO_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "找不到 Python 3。请先安装 Python，然后重新打开此启动器。"
  read "?按回车键关闭…"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import gradio" >/dev/null 2>&1; then
  echo "尚未安装 Gradio。请先在项目目录运行："
  echo "  $PYTHON_BIN -m pip install -r requirements.txt"
  read "?按回车键关闭…"
  exit 1
fi

echo "正在启动 China Video Bot…"
echo "如果浏览器没有自动打开，请访问 http://127.0.0.1:7860/"
exec "$PYTHON_BIN" -m launcher.app
