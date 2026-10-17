# RareBridge 群聊工作室

在真正的 Raft 群聊里配置 Grok 角色：编辑提示词和 Skill、保存草稿、应用到独立的 Agent 工作区，再邀请它参与讨论。聊天和身份由 Raft 提供，本项目增加一个本地配置与试玩界面。

## 本地运行

需要 Node.js 24+、pnpm、Docker、tmux，以及已安装并登录的 Grok CLI。

```sh
npm run bootstrap
npm start
```

打开 http://127.0.0.1:4318 。选择角色，编辑后点击“保存并应用到 Grok”，选择要邀请的角色并发送消息。“打开完整 Raft”可进入多人、频道、线程及消息历史界面。首次应用会创建本项目专用的私人频道，并加入本地种子成员，方便第二个浏览器验证多人聊天。

已有**独立的、版本一致**的 Raft 检出时：

```sh
npm run bootstrap -- --runtime /absolute/path/to/isolated/raft
```

锁定上游 `botiverse/raft-source@05f7d8fd77d2535f993d5d90b85118438bc18216`，避免把未知上游版本当作已验证版本。不要指向正在承担其他任务的 Raft 工作目录。默认依赖安装和完整开发栈启动可能需要数分钟。bootstrap 的套餐配置仅针对新建本地开发数据库，不修改任何托管服务账户。

本地种子用户：`dev@slock.ai`（主用户）、`member@slock.ai`（第二位群友），密码均为上游开发种子密码 `password123`。**开发栈带有公开种子账户，不能直接暴露公网。** 云端应采用独立生产配置，清除种子账户并重新配置身份、持久化和Secrets。

## 三个起始角色

- **小桥**：主持讨论，保留分歧，提出下一问题。
- **小记**：区分已知事实与待确认内容。
- **小伴**：倾听与回应，不伪造亲身经历。

这些是可修改的教学模板，不是已经验证的小Q工作方法或医学知识库。点击“新增角色”可以另建角色；初次应用后保留固定账号名。

## 配置怎样生效

草稿保存在 `.local/state.json`，不会自动改变运行角色。应用时通过 Raft 的授权用户API创建/更新本项目角色，将提示词写入角色的 `AGENTS.md`，Skill写入 `.agents/skills/community-role/SKILL.md`，保存文件指纹及上一版本，再重建模型会话。Raft中的聊天历史保留；重建的模型会话从新指令开始。默认不主动触发其他AI角色，用户选择谁才邀请谁。

工作室只监听回环地址，认证信息留在服务器端，不发送到浏览器。`.local/` 不进入发布仓库。工作室不是生产多租户控制台，也不替代 Raft 的身份与权限。

如需独立 Grok 登录目录或代理，编辑本地 `.local/config.json` 的可选字段：

```json
{
  "grokHome": "/absolute/path/to/private/grok-home",
  "grokEnv": {
    "HTTPS_PROXY": "http://127.0.0.1:7897",
    "HTTP_PROXY": "http://127.0.0.1:7897",
    "NO_PROXY": "localhost,127.0.0.1,::1"
  }
}
```

然后使用该目录登录：`GROK_HOME=/absolute/path/to/private/grok-home grok login`。不需要代理时省略 `grokEnv`。不要把认证目录或真实群聊资料提交到仓库。

## 验证

```sh
npm test
```

单元检查覆盖指令文件安装、版本变更、输入校验及符号链接保护。真实连通性还须在界面中发送消息并收到Grok角色回复，检查两位用户互相可见、修改提示词与Skill后新回复采用该版本。纯模拟回复不算通过。

## 许可证与部署

本项目独立新增代码采用 MIT。Raft 源码不复制进本包，通过固定版本单独取得；它仍采用 [FSL-1.1-ALv2](https://github.com/botiverse/raft-source/blob/05f7d8fd77d2535f993d5d90b85118438bc18216/LICENSE)。Raft 是源码可用，不把它整体宣称为MIT项目。

发布顺序：本地端到端通过 → 检查公开文件 → GitHub发布 → Grok云端实际部署。Grok发布网页的能力不等于已经验证支持完整Raft的数据库、Socket.IO和长期daemon；部署结果以实际URL及运行检查为准。

## Living-scale stage snapshot
See [MCP/native card setup and Grok Build handoff](docs/LIVING-SCALE.md). Source delivery includes no patient records or credentials; full correlated public message acceptance remains incomplete.
