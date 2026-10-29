# Qoder Sites 展示站

本目录是「接力板」的项目展示页静态源文件，与看板应用本身相互独立：

- `index.html` / `styles.css` / `app.js`：单页展示站；
- `assets/`：看板真实运行截图；
- 展示的看板采用单命令启动，应用会自动管理真实 PowerContext，不依赖预先运行的外部服务；
- 不包含 `.qoder.site` 描述文件或任何账号/凭据信息。

展示站通过 Qoder Sites 发布，线上地址以根目录 `README.md` 和 `answers/parallel/DS-06.md` 为准。发布时以本目录作为静态输出目录，无需构建步骤。
