# 📋 联调凭据收集表（填好直接发给我即可）

> 复制下面内容，把 `?` 换成你的实际值，发回给我，我就一次把所有通道配置好并联调。不需要的项留空/删掉即可。

## 1. WOA（WPS 协作 / WPS365）— 主要通道

在 [WPS 开放平台](https://open.wps.cn/) 或 [jp-open.wps.com](https://jp-open.wps.com/) 建内部企业应用、开机器人/消息能力、消息模式选 **HTTP 回调模式** 后：

```
WOA_APP_ID = ?
WOA_SECRET_KEY = ?
WOA_ENCRYPT_KEY =          # 若回调开了加密才需要
WOA_API_URL = ?            # 日本平台实际 API 地址（例如 https://openapi.wps.com 之类；CN 是 https://openapi.wps.cn）
```

回调部署方案（三选一，告诉我选哪个）：
- [ ] A. 桥接机器有公网 IP / 已映射端口
- [ ] B. 用内网穿透（frp / cloudflare tunnel / ngrok）
- [ ] C. 用我写好的 `wps_relay.py` 公网中继（需一台公网服务器，我把部署命令给你）

## 2. 飞书（可选）

- [ ] 自定义机器人 webhook（仅发通知，最快）
```
FEISHU_WEBHOOK_URL = ?
```
- [ ] 应用机器人（能双向收发）
```
FEISHU_APP_ID = ?
FEISHU_APP_SECRET = ?
FEISHU_ENCRYPT_KEY = ?     # 若事件订阅开了加密
```

## 3. QQ（可选）

```
QQ_WS_URL = ?             # OneBot11 WS 地址，如 ws://127.0.0.1:3001
QQ_ACCESS_TOKEN = ?       # 若设置了
QQ_SELF_ID = ?            # 机器人 QQ 号
QQ_ALLOW_GROUPS = []      # 可选，只允许的群，留空=全部
QQ_ALLOW_USERS = []       # 可选，只允许的私聊，留空=全部
QQ_ONEBOT_IMPL = ?        # NapCat / Lagrange / LLOneBot / go-cqhttp
```

## 4. 其余拍板项

```
DEFAULT_WORKSPACE_OR_CWD = ?   # 自动建会话的默认目录，例如 D:\mycode\python\local-dev\dsh-im-bridge\workdir
NOTIFICATION_POLICY = ?        # 现在=最终回答+工具结果+完成+确认；要更安静(只发完成+确认)吗？
INSTALL_SERVICE = ?            # Windows 登录自启 / Ubuntu systemd / 先手动跑
EXPOSE_MANAGEMENT_API = ?      # 管理 API 默认只绑本机；要不要给别的机器用(否则保持 no)
```

## 填好后的效果

我拿到后：配好 `config.yaml` → `dsh-im-bridge --config config.yaml --test-notify woa/feishu/qq` 逐一确认 → 起桥接 → 你在各 IM 里发消息就能指挥 dsh，dsh 完成任务/要你确认时推到你手机上。
