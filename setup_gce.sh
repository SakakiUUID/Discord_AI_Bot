#!/bin/bash
set -e

echo "=== 🚀 開始初始化 GCP GCE 主機環境 ==="

# 1. 更新系統套件
echo "📦 升級系統套件..."
sudo apt-get update && sudo apt-get upgrade -y

# 2. 安裝必要基礎工具
echo "🛠️ 安裝必要工具 (curl, git, ufw)..."
sudo apt-get install -y curl git ca-certificates gnupg lsb-release ufw

# 3. 安裝 Docker 與 Docker Compose Plugin
if ! command -v docker &> /dev/null; then
    echo "🐳 安裝 Docker..."
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg || \
    curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker $USER
    echo "✅ Docker 安裝完成！"
else
    echo "✅ Docker 已安裝，跳過此步驟。"
fi

# 4. 驗證與目錄建立
mkdir -p data

echo "=================================================="
echo "🎉 主機環境準備完成！"
echo "請確保目前目錄下已設定好 .env 檔案，隨後執行以下指令啟動 Bot："
echo ""
echo "   docker compose up -d --build"
echo ""
echo "=================================================="
