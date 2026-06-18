# A股早盘快讯自动推送

工作日 8:30（北京时间）自动搜索盘前资讯，生成结构化早盘快讯并通过飞书机器人推送。

## 功能模块
- 隔夜外围市场（美股/欧股/亚太/A50期货）
- 大宗商品早盘行情
- 今日宏观日历（重要事件/数据）
- 盘前重磅新闻（3-5条精选）
- 昨日A股复盘速览
- 今日关注方向+风险提示

## 工作流程
1. GitHub Actions 每个工作日 8:30（北京时间）触发
2. 自动搜索财经资讯（东方财富、同花顺、新浪财经等）
3. 组装为 Markdown 飞书卡片消息
4. 通过飞书群机器人 Webhook 推送到群聊

## 部署方式

### 1. Fork 本仓库
```bash
git clone https://github.com/<your-username>/stock-morning-brief.git
cd stock-morning-brief
```

### 2. 配置 GitHub Secrets
在仓库 Settings → Secrets and variables → Actions → Secrets 中添加：

| Secret 名称 | 说明 |
|---|---|
| `FEISHU_WEBHOOK` | 飞书群机器人 Webhook 地址 |
| `FEISHU_SECRET` | 飞书群机器人签名校验密钥 |

### 3. 启用 Actions
GitHub Actions 默认已配置，无需额外操作。你也可以手动触发测试：
- 进入 Actions 标签页 → "早盘快讯推送" → Run workflow

## 定时规则
- **UTC 时间**：每周一至周五 00:30
- **北京时间**：每周一至周五 08:30
- 注意：GitHub Actions 定时触发可能有 5-15 分钟延迟

## 技术栈
- Python 3（纯标准库，无第三方依赖）
- HMAC-SHA256 签名校验
- 飞书卡片消息（interactive msg_type）

## 项目结构
```
.
├── .github/
│   └── workflows/
│       └── morning-brief.yml   # GitHub Actions 工作流
├── feishu_push.py              # 飞书消息推送核心脚本
└── README.md
```
