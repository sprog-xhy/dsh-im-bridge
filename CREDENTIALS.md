# 📋 联调凭据收集表（填好直接发给我即可）

> 复制下面内容，把 `?` 换成你的实际值，发回给我，我就一次把飞书/QQ 通道配置好并联调。不需要的项留空/删掉即可。

## 1. 飞书（主通道）

二选一：

- [ ] **方式 A：自定义机器人 webhook（仅发通知，最快，几分钟能通）**
```
FEISHU_WEBHOOK_URL = ?   # 飞书群里加"自定义机器人"得到，形如 https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
```

- [ ] **方式 B：应用机器人（能双向收发，推荐）**
  在[飞书开放平台](https://open.feishu.cn/app)建应用 → 权限管理开通并发布 `im:message` / `im:message:send_as_bot` → 事件订阅选"长连接(WebSocket)"、添加 `im.message.receive_v1` → 拿下面三项：
```
FEISHU_APP_ID = ?
FEISHU_APP_SECRET = ?
FEISHU_ENCRYPT_KEY = ?   # 若事件订阅开了加密
```

## 2. QQ（主通道）

需要一个 OneBot11 协议端（NapCat / Lagrange / LLOneBot / go-cqhttp），用你的 QQ 小号跑起来，开 **WebSocket 服务器** 模式：
```
QQ_WS_URL = ?            # 如 ws://127.0.0.1:3001
QQ_ACCESS_TOKEN = ?      # 若设置了
QQ_SELF_ID = ?           # 机器人 QQ 号（避免回显）
QQ_ONEBOT_IMPL = ?       # NapCat / Lagrange / LLOneBot / go-cqhttp
QQ_ALLOW_GROUPS = []     # 可选，只允许的群，留空=全部
QQ_ALLOW_USERS = []      # 可选，只允许的私聊，留空=全部
```

## 3. 其余拍板项

```
DEFAULT_WORKSPACE_OR_CWD = ?   # 自动建会话的默认目录，例如 D:\mycode\python\local-dev\dsh-im-bridge\workdir
NOTIFICATION_POLICY = ?        # 现在=最终回答+工具结果+完成+确认；要更安静(只发完成+确认)吗？
INSTALL_SERVICE = ?            # Windows 登录自启 / Ubuntu systemd / 先手动跑
EXPOSE_MANAGEMENT_API = ?      # 管理 API 默认只绑本机；要不要给别的机器用(否则保持 no)
```

## 填好后的效果

我拿到后：配好 `config.yaml` → `dsh-im-bridge --config config.yaml --test-notify feishu/qq` 逐一确认 → 起桥接 → 你在飞书/QQ 里发消息就能指挥 dsh，dsh 完成任务/要你确认时推到你手机上。

> 注：WOA（WPS 协作）通道因需要管理员审核已弃用，不在联调范围；代码保留但默认禁用。
