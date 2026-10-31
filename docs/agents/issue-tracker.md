# 本機文件工作流

使用者已指定研究與設計寫入專案資料夾；目前沒有 Git remote 或外部 issue tracker。依此採本機 Markdown，不另建立 GitHub／GitLab issue。

- 第一版唯一規格為 [spec.md](../spec.md)，技術決策為 [design.md](../design.md)。技能要求「publish spec」時更新此處，不再複製到 .scratch。
- [implementation.md](../implementation.md) 是開發順序，不是已建立的票單。
- 開始功能開發後，每票一檔：`docs/tasks/NN-short-name.md`，包含目標、驗收、相依票與 `Status:`。目前票單為 `docs/tasks/01`～`14`。
- 已確認規格不代表所有外部相容性已驗證；涉及候選模型的票，須明列整合檢查前置條件。
- 技能的預設發佈／再次訪談流程不覆蓋使用者已授權的本機文件範圍。
