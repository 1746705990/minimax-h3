# README 增补：新环境复现依赖要点

> 增补于 2026-08-12，对应主 README 第 3B 步（ComfyUI 路线）。
> 三条原则：torch 三件套同批装锁 cu126；装完先跑 import 验证再启动；大文件必做 sha256 校验。

## 一、依赖避雷（插入主 README 第 3B 步 torch 安装之后）

```bash
# 1) torch 三件套：必须同批安装、锁死版本
pip install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu126
# 验证（必须是 2.7.1+cu126 / 0.22.1+cu126 / 2.7.1）：
python3 -c "import torch,torchvision,torchaudio; print(torch.__version__, torchvision.__version__, torchaudio.__version__)"
# 注意：任何后续 pip 操作若把其中一个升级/降级，会出现
# libcudart.so.13 之类的玄学报错，发现漂移用 --force-reinstall --no-deps 拉回

# 2) flash-attn：不要装
# 预编译 wheel 与 torch 2.7.1 二进制不兼容（undefined symbol），
# 只影响 kornia 里的人脸匹配等可选节点，卸了零影响：
pip uninstall flash-attn -y 2>/dev/null; true

# 3) SageAttention 单独装：
pip install sageattention

# 4) comfy_kitchen 0.2.28 在 torch<2.8 下启动报 infer_schema ValueError
# （非致命但刷屏），补丁见附录 A
```

## 二、附录 A：comfy_kitchen 兼容补丁（torch < 2.8 必做）

comfy_kitchen 0.2.28 的算子注册使用了 `list[int]` 等新式类型注解，
torch 2.7.1 的 infer_schema 不支持（2.8 才支持），ComfyUI 启动时会
打印大段 ValueError 堆栈（非致命，但建议修复）。执行：

```bash
python3 - <<'EOF'
import glob
for p in glob.glob('/usr/local/lib/python3.10/dist-packages/comfy_kitchen/**/*.py', recursive=True):
    src = open(p).read()
    orig = src
    src = src.replace('list[int]', 'List[int]').replace('list[bool]', 'List[bool]')
    src = src.replace('float | None', 'Optional[float]').replace('int | None', 'Optional[int]')
    if src != orig:
        if 'from typing import' not in src:
            src = 'from typing import List, Optional\n' + src
        open(p, 'w').write(src)
        print('patched:', p)
# 修复 from __future__ 必须在文件首部的规则冲突
for p in glob.glob('/usr/local/lib/python3.10/dist-packages/comfy_kitchen/**/*.py', recursive=True):
    lines = open(p).read().splitlines(keepends=True)
    if lines and lines[0].strip() == 'from typing import List, Optional':
        rest = lines[1:]
        idx = next((i for i, l in enumerate(rest) if l.startswith('from __future__')), None)
        if idx is not None:
            rest.insert(idx + 1, lines[0])
            open(p, 'w').write(''.join(rest))
            print('fixed:', p)
print('done')
EOF

# 验证（能打印 OK 才算补丁生效）：
python3 -c "import comfy_kitchen; print('comfy_kitchen OK')"
```

## 三、附录 B：数据库位置建议

SQLite 数据库（config.yaml 的 `database`）务必放在**容器本地盘**，
不要放 GPFS 等网络文件系统——网络盘的文件锁语义会导致
`database is locked` 频发，任务无法流转。视频产物目录
`data/outputs/` 可以放网络盘，库文件本身很小，没必要。
（db.py 已内置 locked 自动重试作为兜底，但治本还是放本地。）

## 四、附录 C：ComfyUI 启动脚本已升级为幂等 restart

`start_comfyui.sh` 现在行为：自动杀旧进程（pid 文件失效则按端口特征搜）
→ 确认端口释放 → 启动 → 轮询等就绪（失败自动 tail 日志）。
直接 `bash start_comfyui.sh` 即可完成重启，无需手动 kill。
日志固定写到脚本所在目录的 `logs/comfyui.log`。
