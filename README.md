# Chrome Cookie Migrator for macOS

在两台 Mac 之间迁移 Chrome 的全部 Cookie，实现"在 A 机器登录的所有网站，在 B 机器上也保持登录"。

---

## 目录

- [工作原理](#工作原理)
- [环境要求](#环境要求)
- [安装](#安装)
- [完整迁移流程](#完整迁移流程)
- [命令参考](#命令参考)
- [验证迁移是否成功](#验证迁移是否成功)
- [回滚（恢复原始状态）](#回滚恢复原始状态)
- [常见问题](#常见问题)
- [安全注意事项](#安全注意事项)

---

## 工作原理

macOS 上 Chrome 使用 Keychain 中的 "Chrome Safe Storage" 密码，经 PBKDF2 派生出 AES-128 Key，用 AES-128-CBC 加密每一条 Cookie 的值。由于每台 Mac 的 Keychain 密码不同，**不能直接拷贝 Cookie 数据库文件**。

本工具的做法：

1. **源机器**：读取 Cookie 数据库 → 用源机器的 Key 解密全部 Cookie → 导出为明文 JSON
2. **传输**：将 JSON 文件通过 AirDrop / U 盘 / scp 等方式传到目标机器
3. **目标机器**：读取 JSON → 用目标机器的 Key 重新加密 → 写入目标 Cookie 数据库

---

## 环境要求

- macOS（两台机器都需要）
- Python 3.9+（macOS 自带或通过 Homebrew 安装）
- Google Chrome（至少运行过一次，以生成 Keychain 密钥）

---

## 安装

两台机器都需要安装。

```bash
# 1. 将项目复制到目标机器（可通过 git clone、AirDrop、U 盘等）
#    假设项目在 ~/chrome-cookie-migrator

# 2. 创建虚拟环境并安装依赖
cd ~/chrome-cookie-migrator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果 `pip install` 报 PEP 668 错误，请确保使用了虚拟环境（`source .venv/bin/activate`）。

---

## 完整迁移流程

### 第一步：在源机器上导出 Cookie

```bash
# 0. 进入项目目录并激活虚拟环境
cd ~/chrome-cookie-migrator
source .venv/bin/activate

# 1. 完全退出 Chrome（Cmd+Q，不是关闭窗口）

# 2. 查看有哪些 Profile（如果你只有一个账号可跳过）
python cookie_migrator.py list-profiles
# 输出示例：
#   Available Chrome profiles:
#     - Default  (1632 KB)
#     - Profile 1  (256 KB)

# 3. 导出全部 Cookie
python cookie_migrator.py export -o cookies_backup.json
# 输出示例：
#   Exported 3201 cookies to: cookies_backup.json

# 4.（可选）如果担心安全性，可以导出加密版本
python cookie_migrator.py export -o cookies_backup.enc --encrypt
# 会提示你输入加密密码，在目标机器导入时也需要这个密码
```

导出完成后，当前目录下会生成 `cookies_backup.json`（或 `.enc`）文件。

### 第二步：传输备份文件到目标机器

将 `cookies_backup.json` 文件传到目标机器。推荐方式：

| 方式 | 命令 / 操作 |
|------|------------|
| AirDrop | Finder 中右键文件 → 共享 → AirDrop |
| scp | `scp cookies_backup.json user@target-mac:~/chrome-cookie-migrator/` |
| U 盘 | 复制到 U 盘，再拷贝到目标机器 |
| 共享文件夹 | 放入 iCloud Drive / Dropbox 等同步目录 |

### 第三步：在目标机器上导入 Cookie

```bash
# 0. 进入项目目录并激活虚拟环境
cd ~/chrome-cookie-migrator
source .venv/bin/activate

# 1. 完全退出 Chrome（Cmd+Q）

# 2. 导入全部 Cookie
python cookie_migrator.py import -i cookies_backup.json
# 输出示例：
#   Backed up original database to: .../Cookies.backup.20260305_120000
#   Imported 3201 cookies into profile 'Default'.
#   Restart Chrome to use the imported cookies.

# 3. 打开 Chrome，之前在源机器上登录的网站现在应该也是登录状态
```

---

## 命令参考

### `list-profiles` — 查看可用的 Chrome Profile

```bash
python cookie_migrator.py list-profiles
```

显示本机 Chrome 的所有 Profile 及其 Cookie 数据库大小。

### `export` — 导出 Cookie

```bash
python cookie_migrator.py export [OPTIONS]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-o`, `--output` | 输出文件路径 | `cookies_backup.json` |
| `--profile` | Chrome Profile 名称 | `Default` |
| `--encrypt` | 对导出文件进行 AES-256 加密（会提示输入密码） | 关闭 |

示例：

```bash
# 导出默认 Profile
python cookie_migrator.py export -o cookies_backup.json

# 导出 "Profile 1"
python cookie_migrator.py export -o cookies_backup.json --profile "Profile 1"

# 导出并加密
python cookie_migrator.py export -o cookies_backup.enc --encrypt
```

### `import` — 导入 Cookie

```bash
python cookie_migrator.py import -i <FILE> [OPTIONS]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i`, `--input` | 输入备份文件路径 | （必填） |
| `--profile` | 目标 Chrome Profile 名称 | `Default` |
| `--domains` | 只导入指定域名的 Cookie（逗号分隔） | 全部导入 |
| `--encrypt` | 输入文件是加密的 | 关闭 |

示例：

```bash
# 导入全部
python cookie_migrator.py import -i cookies_backup.json

# 只导入 GitHub 和 Google 的 Cookie
python cookie_migrator.py import -i cookies_backup.json --domains "github.com,google.com"

# 导入加密备份
python cookie_migrator.py import -i cookies_backup.enc --encrypt

# 导入到 "Profile 1"
python cookie_migrator.py import -i cookies_backup.json --profile "Profile 1"
```

---

## 验证迁移是否成功

### 方法 1：直接浏览器验证（最直观）

1. 导入完成后打开 Chrome
2. 访问你之前在源机器上登录过的网站（如 GitHub、Google、飞书等）
3. 如果显示为已登录状态，说明迁移成功

### 方法 2：检查导出的 JSON 文件

```bash
# 查看导出了多少条 Cookie
python3 -c "
import json
with open('cookies_backup.json') as f:
    data = json.load(f)
print(f'总计: {data[\"cookie_count\"]} 条 Cookie')
print(f'导出时间: {data[\"exported_at\"]}')
print(f'Profile: {data[\"profile\"]}')
print()

# 按域名统计
from collections import Counter
domains = Counter(c['host_key'] for c in data['cookies'])
print('Top 10 域名:')
for domain, count in domains.most_common(10):
    print(f'  {domain}: {count} 条')
"
```

### 方法 3：对比导出文件验证（在目标机器上）

导入后再次导出，对比两份 JSON 中的 Cookie 值是否一致：

```bash
# 在目标机器上，导入完成后，退出 Chrome，再导出一份
python cookie_migrator.py export -o cookies_verify.json

# 对比
python3 -c "
import json

with open('cookies_backup.json') as f:
    original = {(c['host_key'], c['name']): c['value'] for c in json.load(f)['cookies']}

with open('cookies_verify.json') as f:
    imported = {(c['host_key'], c['name']): c['value'] for c in json.load(f)['cookies']}

matched = sum(1 for k in original if k in imported and original[k] == imported[k])
missing = sum(1 for k in original if k not in imported)
total = len(original)

print(f'原始 Cookie 数: {total}')
print(f'成功匹配: {matched}')
print(f'未找到: {missing}')
print(f'匹配率: {matched/total*100:.1f}%')
"
```

### 方法 4：验证单个域名的 Cookie

```bash
# 检查某个特定域名的 Cookie 是否已导入
python3 -c "
import json, sys
domain = sys.argv[1]
with open('cookies_backup.json') as f:
    cookies = [c for c in json.load(f)['cookies'] if domain in c['host_key']]
print(f'{domain} 相关的 Cookie ({len(cookies)} 条):')
for c in cookies:
    val_preview = c['value'][:40] + '...' if len(c['value']) > 40 else c['value']
    print(f'  {c[\"host_key\"]} / {c[\"name\"]} = {val_preview}')
" github.com
```

---

## 回滚（恢复原始状态）

导入操作会自动备份原始 Cookie 数据库。如果需要恢复：

```bash
# 1. 完全退出 Chrome

# 2. 找到备份文件
ls ~/Library/Application\ Support/Google/Chrome/Default/Cookies.backup.*
# 输出示例：
#   Cookies.backup.20260305_120000

# 3. 恢复备份
cp ~/Library/Application\ Support/Google/Chrome/Default/Cookies.backup.20260305_120000 \
   ~/Library/Application\ Support/Google/Chrome/Default/Cookies

# 4. 重新打开 Chrome
```

---

## 常见问题

### Q: 运行时报 "Google Chrome is currently running"

必须完全退出 Chrome。按 `Cmd+Q`（不是点击窗口关闭按钮）。如果仍然报错：

```bash
# 强制结束 Chrome 进程
killall "Google Chrome"
```

### Q: 运行时报 "Could not retrieve Chrome Safe Storage password"

Chrome 还没有在这台机器上运行过。先打开 Chrome，随便浏览一个网页，然后退出后再试。

### Q: 导入后某些网站仍然需要重新登录

可能的原因：
- **Session Cookie**：该网站使用无过期时间的 Session Cookie，这类 Cookie 的有效性可能绑定了 IP 或设备指纹
- **安全校验**：部分网站（如银行、支付）会检测设备变化并强制重新登录
- **Cookie 已过期**：导出和导入之间间隔过长，Cookie 已过期

### Q: 两台 Mac 的 Chrome 版本不同会有影响吗？

通常没有问题。Chrome Cookie 数据库的表结构在版本间保持向后兼容。工具会自动读取目标数据库的实际列名进行适配。

### Q: 可以只迁移特定网站的 Cookie 吗？

可以，使用 `--domains` 参数：

```bash
python cookie_migrator.py import -i cookies_backup.json --domains "github.com,google.com,feishu.cn"
```

域名匹配规则是后缀匹配，所以 `google.com` 会匹配 `accounts.google.com`、`mail.google.com` 等子域。

### Q: 可以多次导入吗？

可以。导入使用 `INSERT OR REPLACE` 策略，相同 (host_key, name, path) 的 Cookie 会被覆盖，不会产生重复。

---

## 安全注意事项

- 导出的 JSON 文件包含所有网站的会话信息（等同于你的登录态），**请勿发送给他人或上传到公开位置**
- 推荐使用 `--encrypt` 参数导出加密备份
- 迁移完成后，**删除备份文件**：
  ```bash
  rm cookies_backup.json
  # 或
  rm cookies_backup.enc
  ```
- 如果通过网络传输（如 scp），建议使用加密备份
