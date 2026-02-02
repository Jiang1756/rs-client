#!/usr/bin/env python3
"""
Rs_gh_actions.py - RustDesk GitHub Actions 本地控制器

通过 gh CLI 管理 rs-client 仓库的 GitHub Actions 构建流程。
支持代码提交、触发构建、监控状态和失败排查。

使用方式:
    python Rs_gh_actions.py <command> [options]

命令:
    push    - 提交代码到 rs-client 和 hbb_common 子模块
    build   - 提交代码并触发 GitHub Actions 构建
    watch   - 监控最近一次构建状态
    fail    - 查看最近一次失败构建的日志
"""

import argparse
import json
import subprocess
import sys
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


# ============================================================================
# 配置
# ============================================================================

# 仓库路径（脚本现在在 rs-client 目录下）
SCRIPT_DIR = Path(__file__).parent.resolve()
RS_CLIENT_DIR = SCRIPT_DIR  # 脚本本身就在 rs-client 目录下
HBB_COMMON_DIR = RS_CLIENT_DIR / "libs" / "hbb_common"

# GitHub 仓库信息
REPO_OWNER = "Jiang1756"
REPO_NAME = "rs-client"
WORKFLOW_FILE = "flutter-nightly.yml"  # 用于触发构建的 workflow

# 默认超时配置 (秒)
DEFAULT_CMD_TIMEOUT = 300  # 普通命令 5 分钟
GIT_PUSH_TIMEOUT = 600     # git push 10 分钟 (考虑大文件和慢网络)

# 全局 dry-run 标志
DRY_RUN = False


# 颜色输出
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def color_print(msg: str, color: str = Colors.ENDC):
    """带颜色的打印"""
    print(f"{color}{msg}{Colors.ENDC}")


def run_cmd(
    cmd: List[str], 
    cwd: Optional[Path] = None, 
    capture: bool = True,
    timeout: int = DEFAULT_CMD_TIMEOUT
) -> Tuple[int, str, str]:
    """
    执行命令并返回 (return_code, stdout, stderr)
    
    Args:
        cmd: 命令列表
        cwd: 工作目录
        capture: 是否捕获输出
        timeout: 超时时间（秒）
    """
    if DRY_RUN:
        color_print(f"[DRY-RUN] 将执行: {' '.join(cmd)}", Colors.YELLOW)
        return 0, "[dry-run]", ""
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"命令执行超时 ({timeout}秒)"
    except Exception as e:
        return -1, "", str(e)


def run_cmd_live(cmd: List[str], cwd: Optional[Path] = None) -> int:
    """
    执行命令并实时输出
    """
    if DRY_RUN:
        color_print(f"[DRY-RUN] 将执行: {' '.join(cmd)}", Colors.YELLOW)
        return 0
    
    try:
        result = subprocess.run(cmd, cwd=cwd)
        return result.returncode
    except Exception as e:
        color_print(f"执行失败: {e}", Colors.RED)
        return -1


def check_gh_installed():
    """检查 gh CLI 是否已安装并登录"""
    code, _, _ = run_cmd(["gh", "--version"])
    if code != 0:
        color_print("错误: gh CLI 未安装或不在 PATH 中", Colors.RED)
        color_print("请访问 https://cli.github.com/ 安装 gh", Colors.YELLOW)
        sys.exit(1)
    
    code, _, _ = run_cmd(["gh", "auth", "status"])
    if code != 0:
        color_print("错误: gh CLI 未登录", Colors.RED)
        color_print("请执行 'gh auth login' 进行登录", Colors.YELLOW)
        sys.exit(1)


def check_repo_exists():
    """检查仓库目录是否存在"""
    if not RS_CLIENT_DIR.exists():
        color_print(f"错误: 未找到 rs-client 仓库目录", Colors.RED)
        color_print(f"期望路径: {RS_CLIENT_DIR}", Colors.YELLOW)
        sys.exit(1)
    
    if not HBB_COMMON_DIR.exists():
        color_print(f"警告: hbb_common 子模块目录不存在", Colors.YELLOW)
        color_print("尝试初始化子模块...", Colors.CYAN)
        code = run_cmd_live(["git", "submodule", "update", "--init"], cwd=RS_CLIENT_DIR)
        if code != 0:
            color_print("子模块初始化失败", Colors.RED)
            sys.exit(1)


def get_timestamp() -> str:
    """获取当前时间戳 (YYYYMMDD-HHMMSS)"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def get_tag_timestamp() -> str:
    """获取 Tag 格式的时间戳 (YYYY-MMDD-HHMM)"""
    return datetime.now().strftime("%Y-%m%d-%H%M")


def git_has_changes(repo_dir: Path) -> bool:
    """检查仓库是否有未提交的更改"""
    code, stdout, _ = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir)
    return code == 0 and len(stdout) > 0


def git_check_remote_ahead(repo_dir: Path) -> bool:
    """检查远程是否有新提交"""
    # 先 fetch
    run_cmd(["git", "fetch"], cwd=repo_dir, timeout=60)
    # 检查本地是否落后于远程
    code, stdout, _ = run_cmd(
        ["git", "rev-list", "HEAD..@{u}", "--count"], 
        cwd=repo_dir
    )
    if code == 0 and stdout.isdigit():
        return int(stdout) > 0
    return False


def git_pull(repo_dir: Path) -> bool:
    """执行 git pull"""
    color_print("执行: git pull --rebase", Colors.BLUE)
    code, stdout, stderr = run_cmd(
        ["git", "pull", "--rebase"], 
        cwd=repo_dir,
        timeout=120
    )
    if code != 0:
        color_print(f"git pull 失败: {stderr}", Colors.RED)
        return False
    return True


def git_commit_push(repo_dir: Path, repo_name: str, commit_msg: str, auto_pull: bool = True) -> bool:
    """
    对指定仓库执行 git add, commit, push
    返回是否成功
    
    Args:
        repo_dir: 仓库目录
        repo_name: 仓库名称（用于显示）
        commit_msg: 提交信息
        auto_pull: 如果远程有新提交，是否自动 pull
    """
    color_print(f"\n{'='*50}", Colors.CYAN)
    color_print(f"处理仓库: {repo_name}", Colors.BOLD)
    color_print(f"路径: {repo_dir}", Colors.CYAN)
    color_print(f"{'='*50}", Colors.CYAN)
    
    # 检查是否有更改
    if not git_has_changes(repo_dir):
        color_print("没有需要提交的更改，跳过", Colors.YELLOW)
        return True
    
    # git add .
    color_print("执行: git add .", Colors.BLUE)
    code, _, stderr = run_cmd(["git", "add", "."], cwd=repo_dir)
    if code != 0:
        color_print(f"git add 失败: {stderr}", Colors.RED)
        return False
    
    # git commit
    color_print(f"执行: git commit -m \"{commit_msg}\"", Colors.BLUE)
    code, stdout, stderr = run_cmd(["git", "commit", "-m", commit_msg], cwd=repo_dir)
    if code != 0:
        if "nothing to commit" in stderr or "nothing to commit" in stdout:
            color_print("没有需要提交的更改", Colors.YELLOW)
            return True
        color_print(f"git commit 失败: {stderr}", Colors.RED)
        return False
    color_print(stdout, Colors.GREEN)
    
    # 检查远程是否有新提交
    if auto_pull and git_check_remote_ahead(repo_dir):
        color_print("远程有新提交，自动拉取中...", Colors.YELLOW)
        if not git_pull(repo_dir):
            color_print("建议手动解决冲突后重试", Colors.YELLOW)
            return False
    
    # git push
    color_print("执行: git push", Colors.BLUE)
    code, stdout, stderr = run_cmd(
        ["git", "push"], 
        cwd=repo_dir,
        timeout=GIT_PUSH_TIMEOUT
    )
    if code != 0:
        color_print(f"git push 失败: {stderr}", Colors.RED)
        if "rejected" in stderr.lower():
            color_print("提示: 远程有新提交，请先执行 git pull", Colors.YELLOW)
        return False
    color_print("推送成功!", Colors.GREEN)
    
    return True


def wait_for_workflow_start(max_wait: int = 30) -> Optional[str]:
    """
    等待 workflow 开始运行，返回 run_id
    
    Args:
        max_wait: 最大等待时间（秒）
    
    Returns:
        run_id 或 None（如果超时）
    """
    color_print(f"\n⏳ 等待 workflow 启动 (最多 {max_wait} 秒)...", Colors.CYAN)
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        cmd = [
            "gh", "run", "list",
            "-R", f"{REPO_OWNER}/{REPO_NAME}",
            "-w", WORKFLOW_FILE,
            "-L", "1",
            "--json", "databaseId,status,createdAt"
        ]
        
        code, stdout, _ = run_cmd(cmd, cwd=RS_CLIENT_DIR)
        if code == 0 and stdout:
            try:
                runs = json.loads(stdout)
                if runs and runs[0]["status"] in ["queued", "in_progress"]:
                    run_id = runs[0]["databaseId"]
                    color_print(f"✅ Workflow 已启动! Run ID: {run_id}", Colors.GREEN)
                    return str(run_id)
            except json.JSONDecodeError:
                pass
        
        time.sleep(2)
    
    color_print("⚠️ 等待超时，请手动检查 workflow 状态", Colors.YELLOW)
    return None


# ============================================================================
# 命令实现
# ============================================================================

def cmd_push(args):
    """push 命令: 提交代码到两个仓库"""
    global DRY_RUN
    DRY_RUN = getattr(args, 'dry_run', False)
    
    check_gh_installed()
    check_repo_exists()
    
    timestamp = get_timestamp()
    commit_msg = args.message if hasattr(args, 'message') and args.message else f"build: auto commit {timestamp}"
    
    color_print(f"\n提交信息: {commit_msg}", Colors.HEADER)
    
    if DRY_RUN:
        color_print("\n[DRY-RUN 模式] 以下操作不会真正执行:", Colors.YELLOW)
    
    # 先提交子模块
    success = git_commit_push(HBB_COMMON_DIR, "hbb_common", commit_msg)
    if not success:
        color_print("\nhbb_common 提交失败，终止操作", Colors.RED)
        return 1
    
    # 再提交主仓库 (包含子模块引用更新)
    success = git_commit_push(RS_CLIENT_DIR, "rs-client", commit_msg)
    if not success:
        color_print("\nrs-client 提交失败", Colors.RED)
        return 1
    
    color_print("\n✅ 所有仓库提交完成!", Colors.GREEN)
    return 0


def cmd_build(args):
    """build 命令: 提交代码并触发 GitHub Actions 构建"""
    global DRY_RUN
    DRY_RUN = getattr(args, 'dry_run', False)
    
    check_gh_installed()
    check_repo_exists()
    
    # 1. 执行 push
    color_print("\n📦 步骤 1/3: 提交代码", Colors.HEADER)
    ret = cmd_push(args)
    if ret != 0:
        return ret
    
    # 2. 生成 Tag
    tag = args.tag if args.tag else get_tag_timestamp()
    color_print(f"\n🏷️  步骤 2/3: 使用 Tag: {tag}", Colors.HEADER)
    
    # 3. 触发 workflow
    color_print(f"\n🚀 步骤 3/3: 触发 GitHub Actions 构建", Colors.HEADER)
    color_print(f"Workflow: {WORKFLOW_FILE}", Colors.CYAN)
    
    cmd = [
        "gh", "workflow", "run", WORKFLOW_FILE,
        "-R", f"{REPO_OWNER}/{REPO_NAME}",
        "-f", f"upload-tag={tag}"
    ]
    
    color_print(f"执行: {' '.join(cmd)}", Colors.BLUE)
    code, stdout, stderr = run_cmd(cmd, cwd=RS_CLIENT_DIR)
    
    if code != 0:
        color_print(f"触发构建失败: {stderr}", Colors.RED)
        return 1
    
    color_print("\n✅ 构建已触发!", Colors.GREEN)
    color_print(f"Tag: {tag}", Colors.CYAN)
    
    # 等待确认 workflow 启动
    if not DRY_RUN:
        run_id = wait_for_workflow_start()
        if run_id:
            color_print(f"\n查看构建详情: gh run view {run_id} -R {REPO_OWNER}/{REPO_NAME}", Colors.CYAN)
    
    color_print(f"监控构建状态: python {Path(__file__).name} watch", Colors.YELLOW)
    
    return 0


def cmd_watch(args):
    """watch 命令: 监控最近一次构建状态"""
    check_gh_installed()
    
    color_print("\n👁️  查询最近的构建状态...", Colors.HEADER)
    
    # 获取最近的 workflow runs
    cmd = [
        "gh", "run", "list",
        "-R", f"{REPO_OWNER}/{REPO_NAME}",
        "-w", WORKFLOW_FILE,
        "-L", "5"
    ]
    
    code, stdout, stderr = run_cmd(cmd, cwd=RS_CLIENT_DIR)
    if code != 0:
        color_print(f"查询失败: {stderr}", Colors.RED)
        return 1
    
    if not stdout:
        color_print("没有找到构建记录", Colors.YELLOW)
        return 0
    
    color_print("\n最近 5 次构建:", Colors.CYAN)
    print("-" * 80)
    print(stdout)
    print("-" * 80)
    
    # 如果指定了 --follow，则实时监控最新的运行
    if args.follow:
        color_print("\n🔄 实时监控最新构建...", Colors.HEADER)
        cmd = [
            "gh", "run", "watch",
            "-R", f"{REPO_OWNER}/{REPO_NAME}"
        ]
        return run_cmd_live(cmd, cwd=RS_CLIENT_DIR)
    
    return 0


def cmd_fail(args):
    """fail 命令: 查看最近一次失败构建的日志"""
    check_gh_installed()
    
    color_print("\n🔍 查找最近的失败构建...", Colors.HEADER)
    
    # 获取最近失败的 run
    cmd = [
        "gh", "run", "list",
        "-R", f"{REPO_OWNER}/{REPO_NAME}",
        "-w", WORKFLOW_FILE,
        "-s", "failure",
        "-L", "1",
        "--json", "databaseId,displayTitle,conclusion,createdAt"
    ]
    
    code, stdout, stderr = run_cmd(cmd, cwd=RS_CLIENT_DIR)
    if code != 0:
        color_print(f"查询失败: {stderr}", Colors.RED)
        return 1
    
    try:
        runs = json.loads(stdout)
    except json.JSONDecodeError:
        color_print("解析返回数据失败", Colors.RED)
        return 1
    
    if not runs:
        color_print("没有找到失败的构建记录 🎉", Colors.GREEN)
        return 0
    
    run = runs[0]
    run_id = run["databaseId"]
    title = run["displayTitle"]
    created = run["createdAt"]
    
    color_print(f"\n找到失败构建:", Colors.RED)
    color_print(f"  Run ID: {run_id}", Colors.CYAN)
    color_print(f"  标题: {title}", Colors.CYAN)
    color_print(f"  时间: {created}", Colors.CYAN)
    
    # 获取失败的 jobs
    color_print("\n📋 获取失败的 Jobs...", Colors.HEADER)
    cmd = [
        "gh", "run", "view", str(run_id),
        "-R", f"{REPO_OWNER}/{REPO_NAME}",
        "--log-failed"
    ]
    
    color_print("输出失败日志 (可能较长):\n", Colors.YELLOW)
    print("=" * 80)
    return run_cmd_live(cmd, cwd=RS_CLIENT_DIR)


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="Rs_gh_actions.py",
        description="RustDesk GitHub Actions 本地控制器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python Rs_gh_actions.py push              # 仅提交代码
  python Rs_gh_actions.py push -m "feat: xxx"  # 自定义提交信息
  python Rs_gh_actions.py push --dry-run    # 预览模式，不实际执行
  python Rs_gh_actions.py build             # 提交并触发构建
  python Rs_gh_actions.py build -t v1.0.0   # 使用自定义 Tag 构建
  python Rs_gh_actions.py build --dry-run   # 预览构建流程
  python Rs_gh_actions.py watch             # 查看最近构建状态
  python Rs_gh_actions.py watch -f          # 实时监控构建
  python Rs_gh_actions.py fail              # 查看失败日志
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # push 命令
    push_parser = subparsers.add_parser("push", help="提交代码到 rs-client 和 hbb_common")
    push_parser.add_argument("-m", "--message", help="自定义提交信息")
    push_parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    push_parser.set_defaults(func=cmd_push)
    
    # build 命令
    build_parser = subparsers.add_parser("build", help="提交代码并触发 GitHub Actions 构建")
    build_parser.add_argument("-t", "--tag", help="自定义 Release Tag (默认: MMDD-HHMMSS)")
    build_parser.add_argument("-m", "--message", help="自定义提交信息")
    build_parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    build_parser.set_defaults(func=cmd_build)
    
    # watch 命令
    watch_parser = subparsers.add_parser("watch", help="监控最近一次构建状态")
    watch_parser.add_argument("-f", "--follow", action="store_true", help="实时监控构建进度")
    watch_parser.set_defaults(func=cmd_watch)
    
    # fail 命令
    fail_parser = subparsers.add_parser("fail", help="查看最近一次失败构建的日志")
    fail_parser.set_defaults(func=cmd_fail)
    
    args = parser.parse_args()
    
    # 无参数时显示帮助
    if args.command is None:
        parser.print_help()
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
