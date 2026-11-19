# 📖 GCP Compute Engine (GCE) 一步一步部署教學指南

本指南手把手帶您將 **Discord AI Auto Bot** 部署至 GCP GCE 虛擬主機上，實現 **24 小時不間斷常駐運行**。

---

## 📌 步驟總覽 (Overview)

1. **Step 1**: 在 GCP Console 建立 GCE 虛擬主機 (VM Instance)
2. **Step 2**: 設定 GCP 防火牆 (開放 8080 Port 給 Webhook)
3. **Step 3**: 透過 SSH 連線至 GCE 主機並下載專案代碼
4. **Step 4**: 執行自動化腳本安裝 Docker 與配置 `.env` 變數
5. **Step 5**: 啟動服務與檢視 Bot 狀態
6. **Step 6**: (選用) 設定 WebSub 公網 HTTPS 端點 (Cloudflare Tunnel)

---

## Step 1: 建立 GCE 虛擬主機 (VM Instance)

1. 開啟 [GCP Console (Compute Engine)](https://console.cloud.google.com/compute/instances)。
2. 點擊頂部的 **「建立執行個體」(Create Instance)**。
3. 設定參數：
   * **名稱 (Name)**: `dc-auto-bot-vm`
   * **區域 (Region)**: `asia-east1 (台灣)` 或 `us-central1` (享 Free Tier 額度)。
   * **機器設定 (Machine Configuration)**:
     * 選擇 **E2** 類型。
     * **預設集**: 選擇 **`e2-micro`** (2 vCPU, 1GB 記憶體) 或 **`e2-small`** (2 vCPU, 2GB 記憶體，推薦)。
   * **開機磁碟 (Boot Disk)**:
     * 點擊「變更」。
     * **作業系統**: 選擇 **`Ubuntu`** (版本選擇 `Ubuntu 22.04 LTS`) 或 **`Debian`** (`Debian 12`)。
     * 磁碟大小預設 10 GB 即足夠。
   * **防火牆 (Firewall)**:
     * 勾選 🟢 **「允許 HTTP 流量」**
     * 勾選 🟢 **「允許 HTTPS 流量」**
4. 點擊 **「建立」(Create)**，等待主機啟動完成（綠色勾勾）。

---

## Step 2: 設定 GCP 防火牆 (開放 8080 埠)

YouTube 即時通知 (WebSub) 需要存取 Port `8080`：

1. 在 GCP Console 左側選單搜尋 **「VPC 網路」➡️「防火牆」(Firewall)**。
2. 點擊 **「建立防火牆規則」(Create Firewall Rule)**：
   * **名稱**: `allow-8080-inbound`
   * **目標 (Targets)**: 選擇 `網路上的所有執行個體` (All instances in the network)
   * **來源 IP 範圍 (Source IP ranges)**: 輸入 `0.0.0.0/0`
   * **協定與埠 (Protocols and ports)**:
     * 勾選 `指定的協定與埠` -> 勾選 `tcp` -> 輸入 `8080`
3. 點擊 **「建立」(Create)**。

---

## Step 3: SSH 連線主機與下載專案

1. 回到 **Compute Engine ➡️ VM 執行個體** 列表。
2. 在您的 `dc-auto-bot-vm` 旁點擊 **「SSH」** 按鈕，開啟瀏覽器網頁版終端機。
3. 複製專案代碼（或從您的 Git Repo 複製）：

```bash
# 複製專案 (替換為您的 Git 專案網址，或使用指令下載)
git clone <your-github-repo-url> dc_auto_bot
cd dc_auto_bot
```

---

## Step 4: 執行環境初始化與設定 `.env`

1. **執行自動安裝腳本**：
```bash
chmod +x setup_gce.sh
./setup_gce.sh
```

2. **建立與編輯 `.env` 檔案**：
```bash
nano .env
```

貼上並修改您的 Token 與 Key：
```env
DISCORD_TOKEN=your_real_discord_bot_token
GEMINI_API_KEY=your_real_gemini_api_key
GEMINI_MODEL=gemini-3.8-flash

# WEBHOOK 外部存取網址 (若尚未設定域名，可先填 GCE 的外部 IP，例如 http://34.xxx.xxx.xxx:8080)
WEBHOOK_BASE_URL=http://your-gce-external-ip:8080
WEBHOOK_PORT=8080
```
*(按 `Ctrl + O` 再按 `Enter` 儲存，按 `Ctrl + X` 離開)*

---

## Step 5: 使用 Docker Compose 啟動機器人

執行以下指令構建並背景啟動容器：

```bash
sudo docker compose up -d --build
```

### 🔍 常見查看與管理指令：

* **查看運行紀錄 (Logs)**：
  ```bash
  sudo docker compose logs -f
  ```
* **查看容器狀態**：
  ```bash
  sudo docker compose ps
  ```
* **停止機器人**：
  ```bash
  sudo docker compose down
  ```
* **更新程式後重啟**：
  ```bash
  git pull
  sudo docker compose up -d --build
  ```

---

## Step 6: (進階) 設定 WebSub 即時通知公網 HTTPS (Cloudflare Tunnel)

若需使用 YouTube 即時通知 WebSub，YouTube 規定 Callback 必須是 **HTTPS**：

可以使用免費且無需設定 DNS 的 **Cloudflare Tunnel (`cloudflared`)**：

1. 在 GCE 上安裝 `cloudflared`：
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```
2. 啟動即時免費 HTTPS 隧道指向 8080 埠：
   ```bash
   cloudflared tunnel --url http://localhost:8080
   ```
3. 終端機會顯示一個免費得出的網址，例如：
   `https://random-words.trycloudflare.com`
4. 將該網址複製並更新至 `.env` 中的 `WEBHOOK_BASE_URL`，隨後重啟機器人即可！

---

🎉 **恭喜！您的 Discord AI 機器人已成功在 GCP GCE 24 小時常駐運行！**
