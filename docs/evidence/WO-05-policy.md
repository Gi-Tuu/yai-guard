# WO-05 · 策略规则扩充 + 红队语料

> 日期：2026-10-31 ｜ 状态：**离线全绿**（不需要 key / Docker）
> 语料：[`backend/tests/corpus/bypass.jsonl`](../../backend/tests/corpus/bypass.jsonl)（68 条：deny 48 / approval 13 / allow 7）
> 回归：[`backend/tests/test_policy_corpus.py`](../../backend/tests/test_policy_corpus.py)

## 1. 本轮新增规则

| 规则 | 类型 | 拦截对象 | 为什么需要 |
|---|---|---|---|
| `D-RM-ROOT`（扩充边界） | deny | `(rm -rf /)`、`` `rm -rf /` `` | 原正则只认空白/`;`/`&`/`\|` 之前的 `rm`，括号与反引号包裹可绕过 |
| `D-ARG-OUTSIDE` | deny | `cat /etc/passwd`、`cat /workspace/../etc/passwd`、`grep root /etc/passwd` | 文件类工具有路径校验，但**命令参数**没有；只读命令（cat/head/tail/grep/sed/awk/find/stat/file/du/sha256sum/ls）的参数一律限制在 `/workspace` |
| `D-PIPE-SHELL` | deny | `echo x \| sh`、`printf '\x72…' \| sh` | 任意内容管道进 shell；具体规则（curl/base64）覆盖不到编码/拼接变体 |
| `D-INFINITE-LOOP` | deny | `while true; do :; done` | 无界循环属于资源攻击，应尽快失败而不是等 wall-clock |
| `D-FIND-DELETE` | deny | `find / -delete`、`find /workspace -name '*.py' -delete` | `find -delete` 是 rm 的等价物，`find` 本身在白名单里 |

已有规则保持不变（只加不删）。

## 2. 语料覆盖（按类别）

| 类别 | 代表样例 | 期望 |
|---|---|---|
| 引号/嵌套 | `sh -c "rm -rf /"`、`bash -c 'rm -rf /'`、`echo "rm -rf /"` | deny |
| IFS / 变量 | `rm${IFS}-rf${IFS}/`、`rm$IFS-rf$IFS/` | deny |
| 编码/拼接 | `base64 -d <<< … \| sh`、`printf '\x72…' \| sh`、两段式落盘后执行 | deny / approval |
| 包装器 | `env rm -rf /`、`timeout 5 rm -rf /`、`xargs rm` | deny / approval |
| 重定向 | `> /etc/motd`、`echo x >/root/y`、`cat /etc/shadow > /workspace/leak` | deny |
| 路径穿越 | `/workspace/../etc/passwd`、`~/.ssh/id_rsa` | deny |
| 网络 | `169.254.169.254`、`10.0.0.1`、`metadata.google.internal`、pypi/evil 域名 | deny / approval |
| 二进制 | `mkfs.ext4`、`dd of=/dev/sda`、`mount`、`sudo`、`nsenter`、`unshare` | deny |
| 资源 | 两种 fork bomb、`while true` | deny |
| 正常路径（防误拦） | `python3 main.py`、`python3 main.py > out.txt`、`ls /workspace`、`cat main.py`、`pip install numpy` | allow / approval |

## 3. 验收输出

```text
$ python -m unittest discover -s backend/tests -t backend -p "test_policy_corpus.py" -v
test_corpus_has_enough_cases ... ok
test_every_case_matches_expected_effect ... ok
test_main_demo_commands_are_never_denied ... ok
test_every_denial_has_explainable_rule_and_reason ... ok
Ran 4 tests ... OK
```

失败信息会同时打印 `command / expect / actual effect / actual rule / note`，便于定位是规则缺失还是误拦。

## 4. 残余风险（如实记录）

命令过滤**不是安全保证**。以下是已确认的静态策略缺口，依靠容器边界（只读根、cap-drop、pids/mem、`--network=none`、非 root）兜底：

1. **任意代码执行**：`python3 -c "..."`、`python3 main.py` 的内容无法静态判定；策略只能要求审批，真正的隔离在容器。
2. **两段式载荷**：先 `base64 -d > run.sh`（approval），再 `sh run.sh`（wrapper approval）；规则看不到内容。
3. **动态构造**：`$(printf ...)`、变量拼接、从文件读取命令再执行（`sh < file`）可绕过文本正则。
4. **符号链接 / TOCTOU**：路径规范化不解析 symlink；工作区内脚本可指向 `/etc`。容器内 `/etc` 只读且无敏感挂载，影响有限。
5. **误拦代价**：`echo "rm -rf /"`、`echo x | sh` 这类纯文本/无意义命令也会被 deny（fail-closed 选择）。
6. **命令参数限制的副作用**：`ls /usr`、`grep root /etc/passwd` 被拒绝；这是有意的收紧（Agent 只需要 `/workspace`）。

以上结论会原样写进 README 的「策略不是安全保证」一节，避免评审误解。
