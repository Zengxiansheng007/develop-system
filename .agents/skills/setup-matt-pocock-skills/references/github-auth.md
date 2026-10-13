# GitHub 认证与 Issue 操作

用于 GitHub 工单初始化、Token 配置及认证故障。凭据属于当前用户的本机或连接器认证，不属于技能正文或项目文档。

## 选择认证来源

1. 已连接且能访问目标仓库的 GitHub 连接器可直接复用；本机 PAT 不会自动更新连接器身份，两条认证路径分别核验。
2. 使用 gh 时检查命令是否可用；缺失时可用本技能的 Windows Issue 助手及用户加密凭据，或现有连接器，不因加载技能自动安装 gh。调用时显式指定目标仓库。
3. 没有可用认证时指导用户在本机认证界面或终端操作。新 Token 由用户本地输入，不索取聊天中的明文。若凭据已贴入聊天或公开文件，要求撤销并本地换新；不回显、保存或用已暴露值建立新认证。

## Token 权限

推荐细粒度 PAT，只选择目标仓库。Issue 创建、评论、标签、更新和关闭需要 Issues: Read and write，加默认 Metadata: Read-only。仅在需要读取私有代码、推送或 PR 操作时增加对应 Contents / Pull requests 权限；不要为单纯工单操作要求管理权限。

项目配置仅记录主机、owner/repo、认证方式、权限要求和核验状态。不得将 Token 放入 SKILL.md、AGENTS.md、registry、工单正文、远程 URL、命令参数、日志或版本库。

## Windows 本地 Token 文件与加密存储

用户提供本地 Token 文件时，用 [Issue 助手](../scripts/github-issues.ps1) 的 Import 操作读取，命令只携带文件路径。助手在本机解析唯一 PAT，先核验经认证身份、仓库和 Issue 读取，再以 Windows CurrentUser DPAPI 加密保存到当前用户 LocalApplicationData/Codex/credentials。目录 ACL 限制当前用户与 SYSTEM；跨用户或机器不能直接复用。

```powershell
& '<技能目录>\scripts\github-issues.ps1' -Action Import -Repository 'OWNER/REPO' -TokenFile '<用户指定的本地文件>'
& '<技能目录>\scripts\github-issues.ps1' -Action Check -Repository 'OWNER/REPO'
& '<技能目录>\scripts\github-issues.ps1' -Action List -Repository 'OWNER/REPO'
```

原始 Token 文件保持用户管理；提醒它仍是明文，不擅自删除。Token 轮换后重新 Import。加密文件放在用户凭据目录，不随技能发布。

助手直接调用 GitHub REST API，无需 gh。已授权的真实工单操作可用 View / Create / Edit / Comment / Close；写正文用 BodyFile，标签用 Labels，单工单操作用 IssueNumber。Edit 的 Labels 会替换整组标签，更新前读取已有标签并保留必要值。按项目工单约定查重、核验发布结果；不把 token 检查变成测试工单创建。请求失败只报告 HTTP 状态，不输出授权头。

Check 的结果只证明身份和读取；issue_write 标记 not tested。真实写操作成功后再记录其权限证据。View 返回工单正文；检查前确认其内容适合进入工具输出。

## 本机 gh 认证

- 推荐交互式 `gh auth login --hostname github.com --web`。gh 通常使用系统凭据存储，但可能回退明文文件；核验实际存储方式后才声称安全持久化，不使用 --insecure-storage。
- 细粒度 PAT 按 gh 官方建议使用 GH_TOKEN，由本机秘密存储或用户本地交互输入注入当前进程。不要将带真实值的赋值命令提交给工具或写进终端历史；不要把 Token 持久化为普通用户环境变量。
- PowerShell 本地临时输入示例（仅由用户在自己的终端执行；脚本不含 Token 值）：

```powershell
$githubPatSecure = Read-Host '输入新的 GitHub Token' -AsSecureString
$githubPatPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($githubPatSecure)
try {
    $env:GH_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($githubPatPointer)
    gh api repos/OWNER/REPO --jq '.full_name'
    gh issue list --repo OWNER/REPO --limit 1
} finally {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($githubPatPointer)
    $githubPatSecure.Dispose()
}
```

OWNER/REPO 替换为实际仓库。示例只在操作期间注入凭据，结束即清除，不提供跨会话持久认证。gh 缺失时先完成安装；普通终端环境不会自动注入已经运行的 Codex 桌面进程。要跨会话复用 PAT，应使用系统秘密存储及受控的按需注入，或保留现有连接器认证，不能靠把值写入技能实现。

## 核验与故障

- 仅输出仓库名、认证是否成功及非敏感权限结论。不要执行 gh auth token、--show-token、环境变量转储或开启 HTTP 调试日志。
- 公共仓库读取成功不证明认证或写权限；需要时请求经认证身份信息核对账号。未实际核验的权限标记“未验证”。Issue 写权限仅依据明确权限证据或用户授权的真实工单操作确认，不自行创建测试工单。
- 401 提示凭据无效或过期；403/404 检查目标仓库授权、权限、组织审批和 SSO，不反复重试或直接扩大权限。
- 返回配置完成情况与待人工步骤，不把认证配置等同部署、代码推送或消息授权。受管调用沿用现有回流协议。

## 官方依据

- [GitHub Token 管理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [细粒度 Token 权限](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- [gh auth login](https://cli.github.com/manual/gh_auth_login)
- [gh 环境变量与优先级](https://cli.github.com/manual/gh_help_environment)
