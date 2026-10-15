# 交给 Grok Build 的云端接续

仓库：https://github.com/CardinalWalls/rarebridge-group-chat

本地已验证的是完整Raft + 两个人类用户 + 两个真实Grok CLI角色，包含提示词/Skill修改后生效。此仓库是新增工作室代码，Raft按bootstrap锁定版本单独下载。**当前没有云端部署URL。**

## 可直接交给云端的提示词

请读取并继续开发 https://github.com/CardinalWalls/rarebridge-group-chat 。这是已经在本地通过真实群聊验证的项目，不要改成仅模拟聊天的静态页面。

先检查你的云环境是否支持完整Raft依赖：长期运行的Node服务、PostgreSQL、Redis、S3兼容对象存储、Socket.IO/WebSocket、能够运行Grok CLI的daemon及独立的登录/密钥配置。Raft上游固定版本和许可证见README。检查完请说明具体可用项，缺项不假设存在。

如支持，按云端生产方式改造并部署：将本地bootstrap与开发种子隔离，不部署公开测试密码；把本地单机管理界面的服务端认证与工作区写入改为适合云环境的受认证管理操作；Secrets从云端秘密配置读取，不能进入仓库或浏览器。保留完整Raft群聊，允许配置角色提示词和Skills，并确保Grok是真实执行进程。不要仅代理到原作者电脑。

验收时给出实际访问URL，创建两个测试用户，进入同一测试群；两角色分别收到点名并产生真实回复；只在新版Skill里写测试口令，再通过群消息要求执行该Skill，验证回复采用新版本；重启后消息和配置仍可恢复。报告实际执行了哪些步骤，不把构建成功当成部署成功。

若云环境缺少必要依赖，明确列出缺项以及平台提供的替代服务，并先征求对部署形态的选择，不自动替换成仿制群聊或其他云平台。保留已完成的本地成果。

## 当前入口依据

- [Grok Build官方入口](https://grok.com/?mode=build&referrer=website)
- [官方发布说明](https://x.ai/news/grok-build-for-everyone)：支持发布grok.me应用，不能据此直接推定支持完整Raft服务栈。
- 本地CLI帮助及已检查文档没有通用应用发布子命令。网页入口在本轮可访问环境显示“Sign in”；尚无已登录的网页构建会话可供执行部署。

GitHub发布、本地实测、云端部署分别记账。用户授权的顺序是先本地通过，再GitHub，再云端；当前前两项已完成，第三项待实际云端会话接续。
