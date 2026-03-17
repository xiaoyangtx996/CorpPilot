#!/bin/bash
# CorpPilot 启动脚本 (Linux/macOS)
# 企业智脑 - 多 Agent 协作系统

echo ""
echo "========================================"
echo "  CorpPilot - 企业智脑"
echo "  多 Agent 协作系统"
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python，请先安装 Python 3.10+"
    exit 1
fi

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 初始化示例数据（如果不存在）
if [ ! -f "data/tasks.json" ]; then
    echo "[初始化] 生成示例数据..."
    python3 scripts/init_sample_data.py --tasks 8
    echo ""
fi

# 同步 Agent 配置
echo "[配置] 同步 Agent 配置..."
python3 scripts/sync_agent_config.py sync > /dev/null 2>&1

# 启动服务器
echo "[启动] 启动 Dashboard 服务器..."
echo ""
echo "访问地址: http://localhost:7891"
echo "看板地址: http://localhost:7891/dashboard"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

python3 dashboard/server.py --host 0.0.0.0 --port 7891
