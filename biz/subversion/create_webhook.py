#!/usr/bin/env python3
# -*- coding=utf-8 -*-

import os
import re
import json
import subprocess
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin
from mimetypes import guess_type
import xml.etree.ElementTree as ET
import asyncio
import httpx
from pydantic import BaseModel, Field

import logging
logger = logging.getLogger(__name__)


class SVNFileAction(Enum):
    """SVN文件操作类型"""
    ADDED = "A"
    DELETED = "D"
    MODIFIED = "M"
    REPLACED = "R"
    
    def __str__(self) -> str:
        return self.value
    
    @property
    def display_text(self) -> str:
        mapping = {
            self.ADDED: "➕ 新增",
            self.DELETED: "❌ 删除",
            self.MODIFIED: "✏️ 修改",
            self.REPLACED: "🔄 替换"
        }
        return mapping[self]
    
    @property
    def display_icon(self) -> str:
        mapping = {
            self.ADDED: "➕",
            self.DELETED: "❌", 
            self.MODIFIED: "✏️",
            self.REPLACED: "🔄"
        }
        return mapping[self]


class SVNFileChange(BaseModel):
    """SVN文件变更信息"""
    path: str = Field(..., description="文件路径")
    action: SVNFileAction = Field(..., description="操作类型")
    diff_content: Optional[str] = Field(default=None, description="差异内容")
    old_revision: Optional[str] = Field(default=None, description="旧版本号")
    new_revision: Optional[str] = Field(default=None, description="新版本号")
    lines_added: int = Field(default=0, description="新增行数")
    lines_deleted: int = Field(default=0, description="删除行数")
    is_binary: bool = Field(default=False, description="是否为二进制文件")
    
    def __str__(self) -> str:
        return f"{self.action.display_icon} {self.path}"
    
    def __repr__(self) -> str:
        return f"SVNFileChange(path='{self.path}', action={self.action!r})"
    
    @property
    def display_text(self) -> str:
        """显示友好的变更信息"""
        if self.is_binary:
            return f"{self.action.display_text}: {self.path} (二进制文件)"
        
        if self.action == SVNFileAction.ADDED:
            return f"{self.action.display_text}: {self.path} (+{self.lines_added}行)"
        elif self.action == SVNFileAction.DELETED:
            return f"{self.action.display_text}: {self.path} (-{self.lines_deleted}行)"
        elif self.action == SVNFileAction.MODIFIED:
            return f"{self.action.display_text}: {self.path} (+{self.lines_added},-{self.lines_deleted})"
        else:
            return f"{self.action.display_text}: {self.path}"
    
    @property
    def summary_stats(self) -> str:
        """变更统计摘要"""
        if self.is_binary:
            return "二进制"
        total_changes = self.lines_added + self.lines_deleted
        return f"+{self.lines_added},-{self.lines_deleted} ({total_changes}行变更)"


class SVNRepoInfo(BaseModel):
    """SVN仓库信息"""
    uuid: str = Field(..., description="仓库UUID")
    url: str = Field(..., description="仓库URL")
    relative_url: str = Field(..., description="仓库相对URL")
    root_url: str = Field(..., description="仓库根URL")
    revision: str = Field(..., description="当前版本号")
    
    def __str__(self) -> str:
        return f"SVN仓库 {self.url} (r{self.revision})"
    
    def __repr__(self) -> str:
        return f"SVNRepoInfo(uuid='{self.uuid}', url='{self.url}', revision='{self.revision}')"
    
    @property
    def display_text(self) -> str:
        return f"SVN仓库: {self.url} | 当前版本: r{self.revision} | UUID: {self.uuid[:8]}..."


class SVNWorkingCopyChanges(BaseModel):
    """SVN工作副本变更信息"""
    changed_files: List[SVNFileChange] = Field(default_factory=list, description="变更文件列表")
    repo_uuid: str = Field(..., description="仓库UUID")
    base_revision: str = Field(..., description="基准版本号")
    
    def __str__(self) -> str:
        return f"工作副本变更: {len(self.changed_files)}个文件"
    
    def __repr__(self) -> str:
        return f"SVNWorkingCopyChanges(files={len(self.changed_files)}, base_revision='{self.base_revision}')"
    
    @property
    def display_text(self) -> str:
        """显示友好的变更信息"""
        files_summary = f"{len(self.changed_files)}个文件"
        total_added = sum(f.lines_added for f in self.changed_files)
        total_deleted = sum(f.lines_deleted for f in self.changed_files)
        stats = f"(+{total_added},-{total_deleted})"
        
        return f"工作副本变更: {files_summary} {stats} | 基准版本: r{self.base_revision}"
    
    @property
    def summary_stats(self) -> Dict[str, int]:
        """变更统计摘要"""
        stats = {
            "total_files": len(self.changed_files),
            "added_files": len([f for f in self.changed_files if f.action == SVNFileAction.ADDED]),
            "modified_files": len([f for f in self.changed_files if f.action == SVNFileAction.MODIFIED]),
            "deleted_files": len([f for f in self.changed_files if f.action == SVNFileAction.DELETED]),
            "replaced_files": len([f for f in self.changed_files if f.action == SVNFileAction.REPLACED]),
            "total_lines_added": sum(f.lines_added for f in self.changed_files),
            "total_lines_deleted": sum(f.lines_deleted for f in self.changed_files),
            "binary_files": len([f for f in self.changed_files if f.is_binary])
        }
        return stats


class DiffFormatter:
    """Diff格式化工具类"""
    
    @staticmethod
    def format_to_github_style(svn_diff: str, file_path: str = None) -> str:
        """
        将SVN diff格式转换为GitHub风格的diff
        
        Args:
            svn_diff: SVN原始diff内容
            file_path: 文件路径（用于生成标准化的header）
            
        Returns:
            GitHub风格的diff内容
        """
        if not svn_diff or not svn_diff.strip():
            return ""
            
        lines = svn_diff.split('\n')
        formatted_lines = []
        
        # 跳过SVN特有的头部信息
        in_diff_content = False
        
        for line in lines:
            # 跳过 Index: 行
            if line.startswith('Index:'):
                continue
            
            # 跳过 === 分隔符行
            if line.startswith('====='):
                continue
            
            # 转换文件路径格式
            if line.startswith('---'):
                if file_path:
                    formatted_lines.append(f"--- a/{file_path}")
                else:
                    # 提取文件路径并格式化
                    path_match = re.search(r'---\s+(.+?)\s+\(', line)
                    if path_match:
                        path = path_match.group(1).replace('\\', '/')
                        formatted_lines.append(f"--- a/{path}")
                    else:
                        formatted_lines.append(line)
                in_diff_content = True
                continue
            
            if line.startswith('+++'):
                if file_path:
                    formatted_lines.append(f"+++ b/{file_path}")
                else:
                    # 提取文件路径并格式化
                    path_match = re.search(r'\+\+\+\s+(.+?)\s+\(', line)
                    if path_match:
                        path = path_match.group(1).replace('\\', '/')
                        formatted_lines.append(f"+++ b/{path}")
                    else:
                        formatted_lines.append(line)
                continue
            
            # 保留diff内容
            if in_diff_content:
                formatted_lines.append(line)
        
        return '\n'.join(formatted_lines)
    
    @staticmethod
    def extract_diff_stats(diff_content: str) -> Tuple[int, int]:
        """
        从diff内容中提取统计信息
        
        Args:
            diff_content: diff内容
            
        Returns:
            (lines_added, lines_deleted)
        """
        if not diff_content:
            return 0, 0
            
        lines_added = 0
        lines_deleted = 0
        
        for line in diff_content.split('\n'):
            if line.startswith('+') and not line.startswith('+++'):
                lines_added += 1
            elif line.startswith('-') and not line.startswith('---'):
                lines_deleted += 1
        
        return lines_added, lines_deleted
    
    @staticmethod
    def is_binary_diff(diff_content: str) -> bool:
        """
        检查是否为二进制文件的diff
        
        Args:
            diff_content: diff内容
            
        Returns:
            是否为二进制文件
        """
        if not diff_content:
            return False
            
        # 检查常见的二进制文件标识
        binary_indicators = [
            "Cannot display: file marked as a binary type."
            "svn:mime-type = application/octet-stream"
        ]
        
        content_lower = diff_content.lower()
        return any(indicator.lower() in content_lower for indicator in binary_indicators)


class SVNCommitInfo(BaseModel):
    """SVN提交信息"""
    revision: str = Field(..., description="版本号")
    author: str = Field(..., description="提交作者")
    date: datetime = Field(..., description="提交日期")
    message: str = Field(..., description="提交信息")
    changed_files: List[SVNFileChange] = Field(default_factory=list, description="变更文件列表")
    repo_uuid: str = Field(..., description="仓库UUID")
    
    def __str__(self) -> str:
        return f"r{self.revision} by {self.author}"
    
    def __repr__(self) -> str:
        return f"SVNCommitInfo(revision='{self.revision}', author='{self.author}', files={len(self.changed_files)})"
    
    @property
    def display_text(self) -> str:
        """显示友好的提交信息"""
        files_summary = f"{len(self.changed_files)}个文件"
        total_added = sum(f.lines_added for f in self.changed_files)
        total_deleted = sum(f.lines_deleted for f in self.changed_files)
        stats = f"(+{total_added},-{total_deleted})"
        
        return f"r{self.revision} - {self.author}: {files_summary} {stats}"
    
    @property
    def summary_stats(self) -> Dict[str, int]:
        """变更统计摘要"""
        stats = {
            "total_files": len(self.changed_files),
            "added_files": len([f for f in self.changed_files if f.action == SVNFileAction.ADDED]),
            "modified_files": len([f for f in self.changed_files if f.action == SVNFileAction.MODIFIED]),
            "deleted_files": len([f for f in self.changed_files if f.action == SVNFileAction.DELETED]),
            "replaced_files": len([f for f in self.changed_files if f.action == SVNFileAction.REPLACED]),
            "total_lines_added": sum(f.lines_added for f in self.changed_files),
            "total_lines_deleted": sum(f.lines_deleted for f in self.changed_files),
            "binary_files": len([f for f in self.changed_files if f.is_binary])
        }
        return stats
    
    @staticmethod
    def parse_svn_date(date_str: str) -> datetime:
        """
        解析SVN日期格式: 2025-08-18T03:23:30.945026Z
        
        Args:
            date_str: SVN格式的日期字符串
            
        Returns:
            datetime对象
        """
        try:
            # 移除末尾的Z
            if date_str.endswith('Z'):
                date_str = date_str[:-1]
            
            # 尝试解析带微秒的格式
            if '.' in date_str:
                # 格式: 2025-08-18T03:23:30.945026
                dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S.%f')
            else:
                # 格式: 2025-08-18T03:23:30
                dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
            
            # 设置为UTC时区
            return dt.replace(tzinfo=timezone.utc)
            
        except ValueError as e:
            logger.error(f"Failed to parse date '{date_str}': {e}")
            # 返回当前时间作为后备
            return datetime.now(timezone.utc)
    
    def __str__(self) -> str:
        return f"r{self.revision} by {self.author}: {self.message[:50]}..."
    
    def __repr__(self) -> str:
        return f"SVNCommitInfo(revision='{self.revision}', author='{self.author}', date='{self.date.isoformat()}')"
    
    @property
    def display_text(self) -> str:
        file_count = len(self.changed_files)
        date_str = self.date.strftime('%Y-%m-%d %H:%M:%S')
        return f"提交: r{self.revision} | 作者: {self.author} | 时间: {date_str} | 文件: {file_count}个"
    
    @property
    def timestamp(self) -> float:
        """获取时间戳"""
        return self.date.timestamp()
    
    @property
    def formatted_date(self) -> str:
        """获取格式化的日期字符串"""
        return self.date.strftime('%Y-%m-%d %H:%M:%S')
    
    @property
    def iso_date(self) -> str:
        """获取ISO格式的日期字符串"""
        return self.date.isoformat()
    
    @property
    def date_local(self) -> datetime:
        """获取本地时区的时间"""
        return self.date.astimezone()
    
    def format_date(self, fmt: str) -> str:
        """自定义格式化日期
        
        Args:
            fmt: 时间格式字符串，如 '%Y年%m月%d日 %H:%M:%S'
            
        Returns:
            格式化后的时间字符串
        """
        return self.date.strftime(fmt)


class Result(BaseModel):
    """命令执行结果"""
    returncode: int = Field(..., description="返回码")
    stdout: str = Field(..., description="标准输出")
    stderr: str = Field(..., description="错误输出")
    
    @property
    def success(self) -> bool:
        """是否执行成功"""
        return self.returncode == 0
    
    def __str__(self) -> str:
        status = "成功" if self.success else f"失败(code: {self.returncode})"
        return f"执行结果: {status}"
    
    @property
    def display_text(self) -> str:
        if self.success:
            return f"✅ 执行成功"
        else:
            return f"❌ 执行失败 (返回码: {self.returncode})"


class SubversionWebhook:
    """SVN Webhook 创建和管理类"""
    
    # 事件类型常量
    EventTypeHeader = "X-Subversion-Event"
    PostCommitEvent = "Post-Commit"
    PreCommitEvent = "Pre-Commit"
    
    def __init__(self, repo_uri: Optional[str] = None, webhook_endpoint: Optional[str] = None):
        """
        初始化SVN Webhook管理器
        
        Args:
            repo_uri: SVN仓库URI
            webhook_endpoint: Webhook接收端点URL
        """
        self.webhook_endpoint = webhook_endpoint or os.getenv(
            'REVIEW_WEBHOOK_ENDPOINT', 'http://localhost:5001/review/webhook')
        self.repo_uri = repo_uri
        self.abspath, self.username, self.password = Path(repo_uri), '', ''

    def _build_svn_command(self, command: List[str], auth_required: bool = True) -> List[str]:
        """
        构建SVN命令，添加认证和通用参数
        
        Args:
            command: 基础SVN命令列表
            auth_required: 是否需要认证
            
        Returns:
            完整的SVN命令列表
        """
        full_command = ['svn'] + command
        
        # # 添加认证参数
        # if auth_required and self.username:
        #     full_command.extend(['--username', self.username])
        #     if self.password:
        #         full_command.extend(['--password', self.password])
        
        # 添加通用参数
        # full_command.extend([
        #     '--non-interactive',
        #     '--trust-server-cert-failures', 'unknown-ca,cn-mismatch,expired,not-yet-valid,other',
        #     '--no-auth-cache'
        # ])
        
        return full_command

    def svn_command(self, command: List[str], auth_required: bool = True) -> Result:
        """
        执行SVN命令并返回结果

        Args:
            command: SVN命令
            auth_required: 是否需要认证

        Returns:
            返回码、标准输出和错误输出
        """
        svn_bin = os.path.expandvars(os.path.join("$ProgramData", "CodeCheck", "config", "VisualSVN", "bin", "svn.exe"))
        full_command = self._build_svn_command(command, auth_required=auth_required)
        result = subprocess.run(full_command, capture_output=True, text=True, executable=svn_bin, cwd=self.abspath)
        return Result(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr
        )

    def get_repo_info(self) -> Optional[SVNRepoInfo]:
        """
        获取SVN仓库信息
        
        Returns:
            SVNRepoInfo对象或None
        """
        try:
            # 获取仓库信息
            result = self.svn_command(['info', '--xml'])
            if not result.success:
                logger.error(f"Failed to get repo: {result.stderr}")
                return None

            # 解析XML输出
            root = ET.fromstring(result.stdout)
            revision = root.find('entry').attrib['revision']
            uuid = root.find('entry/repository/uuid').text
            url = root.find('entry/url').text
            relative = root.find('entry/relative-url').text
            root_url = root.find('entry/repository/root').text
            self.abspath = Path(root.find('entry/wc-info/wcroot-abspath').text)

            return SVNRepoInfo(
                uuid=uuid,
                url=url,
                relative_url=relative,
                root_url=root_url,
                revision=revision
            )
            
        except subprocess.TimeoutExpired:
            logger.error("SVN command timed out")
            return None
        except Exception as e:
            logger.error(f"Error getting repo info: {e}")
            return None

    def get_file_diff(self, file_path: str, old_revision: str = None, new_revision: str = None, 
                      format_style: str = "github") -> Tuple[Optional[str], int, int, bool]:
        """
        获取文件的diff信息 - 支持多种diff场景
        
        Args:
            file_path: 文件路径
            old_revision: 旧版本号，None表示使用基础版本
            new_revision: 新版本号，None表示使用变更版本
            format_style: diff格式风格，默认"github"
            
        Returns:
            (diff_content, lines_added, lines_deleted, is_binary)
        """
        try:
            # 构建diff命令
            diff_cmd = ['diff']
            
            if old_revision and new_revision:
                # 两个版本之间的diff
                diff_cmd.extend(['-r', f"{old_revision}:{new_revision}"])
            elif old_revision:
                # 指定版本与工作副本的diff
                diff_cmd.extend(['-r', old_revision])
            else: pass
            
            # 添加文件路径
            diff_cmd.append(file_path)
            
            diff_result = self.svn_command(diff_cmd)
            
            if not diff_result.success:
                logger.warning(f"Failed to get diff for {file_path}: {diff_result.stderr}")
                return None, 0, 0, False
            
            diff_content = diff_result.stdout
            
            # 检查是否为二进制文件
            is_binary = DiffFormatter.is_binary_diff(diff_content)
            
            if is_binary:
                return diff_content, 0, 0, True
            
            # 格式化diff内容
            if format_style == "github":
                formatted_diff = DiffFormatter.format_to_github_style(diff_content, file_path)
            else:
                formatted_diff = diff_content
            
            # 统计添加和删除的行数
            lines_added, lines_deleted = DiffFormatter.extract_diff_stats(diff_content)
            
            return formatted_diff, lines_added, lines_deleted, False
            
        except Exception as e:
            logger.error(f"Error getting diff for {file_path}: {e}")
            return None, 0, 0, False

    def get_working_copy_changes(self, commit_files: Optional[List[str]] = None, 
                                 format_style: str = "github") -> Optional[SVNWorkingCopyChanges]:
        """
        获取工作副本的变更信息（相对于BASE版本）
        
        Args:
            commit_files: 指定要检查的文件列表，如果为None则检查所有变更
            format_style: diff格式风格
            
        Returns:
            SVNWorkingCopyChanges对象或None
        """
        try:
            # 获取仓库信息
            repo_info = self.get_repo_info()
            if not repo_info:
                logger.error("Failed to get repository info")
                return None
            
            changed_files = []
            
            if commit_files is not None:
                # 如果指定了文件列表，逐个检查每个文件
                logger.info(f"Checking specific files: {len(commit_files)} files")
                
                for file_path in commit_files:
                    file_change = self._process_single_file_change(
                        file_path, repo_info, format_style
                    )
                    if file_change:
                        changed_files.append(file_change)
                        logger.info(f"Processed specific file change: {file_change.display_text}")
            else:
                # 如果未指定文件列表，获取所有变更文件列表（使用summarize模式）
                logger.info("Getting all working copy changes")
                status_result = self.svn_command(['diff', '--summarize'])
                if not status_result.success:
                    logger.error(f"Failed to get working copy changes: {status_result.stderr}")
                    return None
                
                # 解析状态输出
                for line in status_result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    
                    # 解析状态行：状态码 + 文件路径
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    
                    action_str = parts[0]
                    file_path = ' '.join(parts[1:])  # 处理路径中包含空格的情况
                    
                    # 转换为枚举类型
                    try:
                        action = SVNFileAction(action_str)
                    except ValueError:
                        logger.warning(f"Unknown SVN action: {action_str}, skipping file {file_path}")
                        continue
                    
                    # 获取diff信息
                    diff_content, lines_added, lines_deleted, is_binary = self.get_file_diff(
                        file_path, format_style=format_style
                    )
                    
                    file_change = SVNFileChange(
                        path=file_path,
                        action=action,
                        diff_content=diff_content,
                        old_revision=repo_info.revision,
                        new_revision="working copy",
                        lines_added=lines_added,
                        lines_deleted=lines_deleted,
                        is_binary=is_binary
                    )
                    
                    changed_files.append(file_change)
                    logger.info(f"Processed working copy change: {file_change.display_text}")
            
            return SVNWorkingCopyChanges(
                changed_files=changed_files,
                repo_uuid=repo_info.uuid,
                base_revision=repo_info.revision
            )
            
        except Exception as e:
            logger.error(f"Error getting working copy changes: {e}")
            return None

    def _process_single_file_change(self, file_path: str, repo_info: SVNRepoInfo, format_style: str) -> Optional[SVNFileChange]:
        """
        处理单个文件的变更信息
        
        Args:
            file_path: 文件路径
            repo_info: 仓库信息
            format_style: diff格式风格
            
        Returns:
            SVNFileChange对象或None
        """
        try:
            # 对单个文件执行diff --summarize命令
            status_result = self.svn_command(['diff', '--summarize', file_path])

            # 检查文件是否在版本控制中 (理论上是不会进入这个分支的。因为文件应该已经被添加到版本控制中)
            if not status_result.success:
                # 检查是否是文件不在版本控制中的错误
                if "was not found" in status_result.stderr and "E155010" in status_result.stderr:
                    logger.info(f"File {file_path} is not under version control, treating as ADDED")
                return None
            
            # 解析diff --summarize的输出
            stdout_lines = status_result.stdout.strip().split('\n')
            if not stdout_lines or not stdout_lines[0].strip():
                logger.info(f"No changes found for file {file_path}")
                return None
            
            # 解析状态行：状态码 + 文件路径
            line = stdout_lines[0]
            parts = line.split()
            if len(parts) < 2:
                logger.warning(f"Invalid diff output format for file {file_path}: {line}")
                return None
            
            action_str = parts[0]
            
            # 转换为枚举类型
            try:
                action = SVNFileAction(action_str)
            except ValueError:
                logger.warning(f"Unknown SVN action: {action_str} for file {file_path}")
                return None
            
            # 获取详细的diff信息
            diff_content, lines_added, lines_deleted, is_binary = self.get_file_diff(
                file_path, format_style=format_style
            )
            
            return SVNFileChange(
                path=file_path,
                action=action,
                diff_content=diff_content,
                old_revision=repo_info.revision,
                new_revision="working copy",
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                is_binary=is_binary
            )
            
        except Exception as e:
            logger.error(f"Error processing file change for {file_path}: {e}")
            return None

    def get_commit_info(self, revision: str, format_style: str = "github") -> Optional[SVNCommitInfo]:
        """
        获取指定版本的提交信息 (远端)
        
        Args:
            revision: SVN版本号
            
        Returns:
            SVNCommitInfo对象或None
        """
        try:
            # 获取仓库UUID
            repo_info = self.get_repo_info()
            repo_uuid = repo_info.uuid if repo_info else ''

            # 获取提交日志
            log_result = self.svn_command(['log', '-v', '-r', revision or repo_info.revision, '--xml'])
            if not log_result.success:
                logger.error(f"Failed to get commit log: {log_result.stderr}")
                return None
            
            # 解析XML输出
            root = ET.fromstring(log_result.stdout)
            logentry = root.find('logentry')
            
            if logentry is None:
                logger.error(f"No log entry found for revision {revision}")
                return None
            
            date_str = SVNCommitInfo.parse_svn_date(logentry.find('date').text)
            author = logentry.find('author').text if logentry.find('author') is not None else 'unknown'
            message = logentry.find('msg').text if logentry.find('msg') is not None else ''
            
            # 获取变更文件列表并获取diff信息
            changed_files = []
            paths = logentry.find('paths')
            if paths is not None:
                for path in paths.findall('path'):
                    if path.text:
                        action_str = path.attrib['action']
                        file_path = path.text
                        
                        # 转换为枚举类型
                        try:
                            action = SVNFileAction(action_str)
                        except ValueError:
                            logger.warning(f"Unknown SVN action: {action_str}, skipping file {file_path}")
                            continue
                        
                        # 计算版本号 (TODO: HEAD BASE COMMITTED PREV)
                        old_revision = str(int(revision) - 1) if action != SVNFileAction.ADDED else None
                        new_revision = revision if action != SVNFileAction.DELETED else None
                        
                        # 获取diff信息 (远端)
                        diff_content, lines_added, lines_deleted, is_binary = self.get_file_diff(
                            f"{repo_info.root_url}{file_path}", 
                            old_revision, new_revision, 
                            format_style=format_style
                        )
                        
                        file_change = SVNFileChange(
                            path=file_path,
                            action=action,
                            diff_content=diff_content,
                            old_revision=old_revision,
                            new_revision=new_revision,
                            lines_added=lines_added,
                            lines_deleted=lines_deleted,
                            is_binary=is_binary
                        )
                        
                        changed_files.append(file_change)
                        
                        logger.info(f"Processed file change: {file_change.display_text}")
            
            return SVNCommitInfo(
                revision=revision,
                author=author,
                date=date_str,
                message=message,
                changed_files=changed_files,
                repo_uuid=repo_uuid
            )
            
        except subprocess.TimeoutExpired:
            logger.error("SVN log command timed out")
            return None
        except Exception as e:
            logger.error(f"Error getting commit info: {e}")
            return None

    async def send_webhook_async(self, payload: Dict, event_type: str = "Post-Commit") -> bool:
        """
        异步发送webhook请求
        
        Args:
            payload: 要发送的数据
            event_type: 事件类型
            
        Returns:
            是否发送成功
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "X-Subversion-Event": event_type,
                "X-Subversion-Token": os.getenv('SUBVERSION_ACCESS_TOKEN', 'default-token')
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.webhook_endpoint,
                    json=payload,
                    headers=headers
                )
                
                if response.status_code in [200, 201, 202]:
                    logger.info(f"Webhook sent successfully: {response.status_code}")
                    return True
                else:
                    logger.error(f"Webhook failed with status: {response.status_code}, response: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")
            return False

    def _extract_svn_author(self) -> str:
        """
        从本地配置中提取作者名称

        Returns:
            作者名称
        """
        try:
            svn_auth_dir = os.path.join(os.path.expandvars("$APPDATA"), "Subversion", "auth", "svn.simple")
            users = os.listdir(svn_auth_dir)
            with open(os.path.join(svn_auth_dir, users[0]), 'r') as f:
                author_match = re.search(r'username\s+V\s+\d+\s+(\w+)', f.read())
                if author_match:
                    return author_match.group(1).strip()
        except Exception as e:
            logger.warning(f"Failed to extract SVN author: {e}")
            return "unknown"

    def _extract_repo_name(self, repo_url: str) -> str:
        """
        从SVN仓库URL中提取仓库名称
        
        Args:
            repo_url: SVN仓库URL
            
        Returns:
            仓库名称
        """
        try:
            parsed = urlparse(repo_url)
            # 提取路径的最后一部分作为仓库名
            path_parts = parsed.path.strip('/').split('/')
            if path_parts and path_parts[-1]:
                return path_parts[-1]
            else:
                # 如果路径为空，使用域名
                return parsed.netloc.replace('.', '_')
        except Exception as e:
            logger.warning(f"Failed to extract repo name from {repo_url}: {e}")
            return "unknown_repo"

    def _build_svn_webhook_payload(self, 
                                   event_type: str,
                                   changes_data: Optional[List[SVNFileChange]] = None,
                                   commit_info: Optional[SVNCommitInfo] = None,
                                   commit_message: Optional[str] = None,
                                   repo_info: Optional[SVNRepoInfo] = None) -> Dict:
        """
        构造SVN webhook payload
        
        Args:
            event_type: 事件类型
            changes_data: 文件变更列表
            commit_info: 提交信息
            repo_info: 仓库信息
            
        Returns:
            标准化的webhook payload
        """
        if not repo_info:
            repo_info = self.get_repo_info()
            if not repo_info:
                logger.error("Failed to get repository info for webhook payload")
                return {}

        repo_name = self._extract_repo_name(repo_info.url)
        
        # 构造changes数组（参考GitLab/GitHub格式）
        changes = []
        if changes_data:
            for file_change in changes_data:
                change = {
                    'diff': file_change.diff_content or '',
                    'new_path': file_change.path,
                    'old_path': file_change.path,
                    'additions': file_change.lines_added,
                    'deletions': file_change.lines_deleted,
                    # SVN特有字段
                    'action': file_change.action.value,
                    'is_binary': file_change.is_binary,
                    'old_revision': file_change.old_revision,
                    'new_revision': file_change.new_revision
                }
                changes.append(change)

        # 构造commits数组
        commits = []
        if commit_info:
            commit = {
                'id': commit_info.revision,
                'message': commit_info.message,
                'author': commit_info.author,
                'timestamp': commit_info.iso_date,
                'created_at': commit_info.iso_date,
                # TODO: 待确认是否需要构造具体的提交URL
                'url': f"{repo_info.url}?r={commit_info.revision}",
            }
            commits.append(commit)

        # 构造主payload，参考GitLab webhook格式
        payload = {
            # 事件类型标识
            'event_type': event_type,
            'object_kind': 'svn_commit',
            
            # 仓库信息
            'repository': {
                'uuid': repo_info.uuid,
                'url': repo_info.url,
                'name': repo_name,
                'description': f"SVN Repository: {repo_name}",
                'homepage': repo_info.url,
            },
            
            # 项目信息（参考GitLab）
            # TODO: 待确认webhook_handler对这些字段的具体需求
            # 'project': {
            #     'id': hash(repo_info.uuid) % 1000000,  # 生成一个数字ID
            #     'name': repo_name,
            #     'description': f"SVN Repository: {repo_name}",
            #     'web_url': repo_info.url,
            #     'visibility_level': 0,
            #     'default_branch': 'trunk',
            # },
            
            # 提交相关信息
            'object_attributes': {
                'revision': commit_info.revision if commit_info else repo_info.revision,
                'message': commit_message or '',
                'author': commit_info.author if commit_info else self._extract_svn_author(),
                'created_at': commit_info.iso_date if commit_info else datetime.now(timezone.utc).isoformat(),
                'updated_at': commit_info.iso_date if commit_info else None,
                'url': f"{repo_info.url}?r={commit_info.revision}" if commit_info else f"{repo_info.url}?r={repo_info.revision}",
                'action': event_type,
                'target_branch': 'trunk',
                'source_branch': 'trunk',
                'state': 'opened' if event_type == self.PreCommitEvent else 'merged',
            },
            
            # 变更和提交数据
            'changes': changes,
            'commits': commits,
            
            # SVN特有信息
            'svn_info': {
                'repository_uuid': repo_info.uuid,
                'repository_url': repo_info.url,
                'repository_root': repo_info.root_url,
                'revision': commit_info.revision if commit_info else None,
                'event_type': event_type
            }
        }
        
        # 事件特定字段
        if event_type == self.PreCommitEvent:
            payload['object_attributes']['title'] = 'Pre-Commit validation'
            payload['object_attributes']['description'] = 'SVN pre-commit hook validation'
        else:
            payload['object_attributes']['title'] = commit_info.message.split('\n')[0] if commit_info and commit_info.message else 'SVN commit'
            payload['object_attributes']['description'] = commit_info.message if commit_info else 'SVN post-commit notification'

        logger.debug(f"Built SVN webhook payload for {event_type}: {len(changes)} changes, {len(commits)} commits")
        return payload

    def create_pre_commit_hook(self, 
                               commit_files: Optional[List[str]] = None, 
                               commit_message: Optional[str] = None,
                               repo_path: str = None) -> bool:
        """
        创建并触发pre-commit webhook
        获取工作副本变更信息并发送预提交审查请求
        
        Args:
            commit_files: 提交的文件列表（可选）
            commit_message: 提交信息（可选）
            repo_path: SVN仓库路径（可选，用于未来扩展）
            
        Returns:
            是否成功发送webhook
        """
        try:
            logger.info("Creating pre-commit webhook for working copy changes")
            
            # 获取工作副本变更信息
            changes_info = self.get_working_copy_changes(commit_files=commit_files)
            if not changes_info:
                logger.warning("No working copy changes found for pre-commit hook")
                return False
            
            if not changes_info.changed_files:
                logger.info("No files changed in working copy")
                return True  # 没有变更也算成功
            
            # 获取仓库信息
            repo_info = self.get_repo_info()
            if not repo_info:
                logger.error("Failed to get repository info for pre-commit hook")
                return False
            
            # 构造webhook payload
            payload = self._build_svn_webhook_payload(
                event_type=self.PreCommitEvent,
                changes_data=changes_info.changed_files,
                commit_info=None,  # pre-commit时没有commit信息
                commit_message=commit_message,
                repo_info=repo_info
            )
            
            if not payload:
                logger.error("Failed to build webhook payload for pre-commit")
                return False
            
            # 发送webhook
            success = self.send_webhook(payload, event_type=self.PreCommitEvent)
            
            if success:
                logger.info(f"Pre-commit webhook sent successfully: {len(changes_info.changed_files)} files changed")
            else:
                logger.error("Failed to send pre-commit webhook")
            
            return success
            
        except Exception as e:
            logger.error(f"Error in create_pre_commit_hook: {e}")
            return False

    def create_post_commit_hook(self, revision: str = None, repo_path: str = None) -> bool:
        """
        创建并触发post-commit webhook
        获取指定版本的提交信息并发送后提交审查请求
        
        Args:
            revision: SVN版本号（如果不提供，使用当前版本）
            repo_path: SVN仓库路径（可选，用于未来扩展）
            
        Returns:
            是否成功发送webhook
        """
        try:
            # 获取仓库信息以确定revision
            repo_info = self.get_repo_info()
            if not repo_info:
                logger.error("Failed to get repository info for post-commit hook")
                return False
            
            # 如果没有指定revision，使用当前版本
            target_revision = revision or repo_info.revision
            
            logger.info(f"Creating post-commit webhook for revision: {target_revision}")
            
            # 获取提交信息
            commit_info = self.get_commit_info(target_revision)
            if not commit_info:
                logger.error(f"Failed to get commit info for revision: {target_revision}")
                return False
            
            if not commit_info.changed_files:
                logger.info(f"No files changed in revision {target_revision}")
                return True  # 没有变更也算成功
            
            # 构造webhook payload
            payload = self._build_svn_webhook_payload(
                event_type=self.PostCommitEvent,
                changes_data=commit_info.changed_files,
                commit_info=commit_info,
                repo_info=repo_info
            )
            
            if not payload:
                logger.error("Failed to build webhook payload for post-commit")
                return False
            
            # 发送webhook
            success = self.send_webhook(payload, event_type=self.PostCommitEvent)
            
            if success:
                logger.info(f"Post-commit webhook sent successfully for revision {target_revision}: {len(commit_info.changed_files)} files changed")
            else:
                logger.error(f"Failed to send post-commit webhook for revision {target_revision}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error in create_post_commit_hook: {e}")
            return False

    def send_webhook(self, payload: Dict, event_type: str = "Post-Commit") -> bool:
        """
        同步发送webhook请求
        
        Args:
            payload: 要发送的数据
            event_type: 事件类型
            
        Returns:
            是否发送成功
        """
        try:
            return asyncio.run(self.send_webhook_async(payload, event_type))
        except Exception as e:
            logger.error(f"Failed to send webhook synchronously: {e}")
            return False

    def setup_webhook(self, repo_path: str, hook_types: List[str] = None) -> Dict[str, bool]:
        """
        设置SVN仓库的webhook钩子
        
        Args:
            repo_path: SVN仓库的路径
            hook_types: 要创建的钩子类型列表，默认为['post-commit']
            
        Returns:
            每种钩子类型的创建结果
        """
        if hook_types is None:
            hook_types = ['post-commit']
        
        results = {}
        
        # 创建钩子
        for hook_type in hook_types:
            if hook_type == 'post-commit':
                results[hook_type] = self.create_post_commit_hook(repo_path)
            elif hook_type == 'pre-commit':
                results[hook_type] = self.create_pre_commit_hook(repo_path)
            else:
                logger.warning(f"Unsupported hook type: {hook_type}")
                results[hook_type] = False
        
        return results

    def remove_webhook(self, repo_path: str, hook_types: List[str] = None) -> Dict[str, bool]:
        """
        移除SVN仓库的webhook钩子
        
        Args:
            repo_path: SVN仓库的路径
            hook_types: 要移除的钩子类型列表，默认为['post-commit', 'pre-commit']
            
        Returns:
            每种钩子类型的移除结果
        """
        if hook_types is None:
            hook_types = ['post-commit', 'pre-commit']
        
        results = {}
        hooks_dir = Path(repo_path) / 'hooks'
        
        for hook_type in hook_types:
            try:
                hook_script = hooks_dir / hook_type
                if hook_script.exists():
                    hook_script.unlink()
                    results[hook_type] = True
                    logger.info(f"Removed {hook_type} hook: {hook_script}")
                else:
                    results[hook_type] = True  # 文件不存在也算成功
                    logger.info(f"Hook {hook_type} does not exist: {hook_script}")
            except Exception as e:
                logger.error(f"Error removing {hook_type} hook: {e}")
                results[hook_type] = False
        
        return results