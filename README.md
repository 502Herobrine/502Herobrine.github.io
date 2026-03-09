# 欢迎来到我的笔记站点

这是一个基于 [Docsify](https://docsify.js.org/) 的笔记站点，内容由 Obsidian 笔记自动构建而成。

## 使用方法

### 方式一：手动上传

1. 将 Obsidian 笔记文件夹放入本仓库。
2. 推送到 `main` 分支，GitHub Actions 会自动构建并部署。
3. 笔记中的 Obsidian 语法（如 `![[图片.jpg|600]]`）会自动转换为标准格式。

### 方式二：WebDAV 自动同步（坚果云）

通过 WebDAV 连接坚果云，自动检测远程更新、拉取笔记并构建部署。

**配置步骤：**

1. **获取坚果云 WebDAV 应用密码**
   - 登录 [坚果云](https://www.jianguoyun.com/) → 右上角用户名 → 账户信息
   - 安全选项 → 第三方应用管理 → 添加应用 → 生成应用密码

2. **在 GitHub 仓库中配置 Secrets**
   - 进入仓库 → Settings → Secrets and variables → Actions
   - 添加两个 Repository Secret：
     - `WEBDAV_USER` — 坚果云账号邮箱
     - `WEBDAV_PASSWORD` — 上一步生成的应用密码

3. **修改同步配置**
   - 编辑 `webdav_config.yml`：
     - 将 `remote_path` 改为你坚果云中笔记所在的文件夹名
     - 在 `whitelist` 中添加需要同步的文件/文件夹模式
     - 在 `exclude` 中添加不需要同步的路径

4. **推送到 `main` 分支**
   - GitHub Actions 会每 30 分钟自动检查坚果云是否有更新
   - 检测到变更时自动拉取、提交、并触发构建部署
   - 也可以在 Actions 页面手动触发同步

## 笔记目录结构

```
├── 主题文件夹/
│   ├── 笔记1.md
│   ├── 笔记2.md
│   └── images/          ← 该文件夹下 md 引用的图片
│       ├── 图片1.jpg
│       └── 图片2.png
└── 另一个主题/
    ├── 笔记3.md
    └── images/
        └── 图片3.jpg
```

请使用左侧导航栏浏览笔记内容。
