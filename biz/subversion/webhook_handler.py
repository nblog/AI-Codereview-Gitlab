#!/usr/bin/env python3
# -*- coding=utf-8 -*-

import os
import re
from typing import List, Dict, Optional
from biz.utils.log import logger


def filter_changes(changes: List[Dict]) -> List[Dict]:
    """
    过滤SVN变更数据，只保留支持的文件类型以及必要的字段信息
    
    Args:
        changes: SVN变更列表，每个元素包含diff, new_path, additions, deletions等字段
        
    Returns:
        过滤后的变更列表
    """
    # 从环境变量中获取支持的文件扩展名
    supported_extensions = os.getenv('SUPPORTED_EXTENSIONS', '.java,.py,.php').split(',')
    
    # 过滤删除的文件（SVN中action为'D'的文件）
    filter_deleted_files_changes = [
        change for change in changes 
        if change.get("action") != "D"  # SVN特有：过滤删除的文件
    ]
    
    logger.info(f"SUPPORTED_EXTENSIONS: {supported_extensions}")
    logger.info(f"After filtering deleted files: {len(filter_deleted_files_changes)} files")
    
    # 过滤 `new_path` 以支持的扩展名结尾的元素，仅保留必要字段
    filtered_changes = [
        {
            'diff': item.get('diff', ''),
            'new_path': item['new_path'],
            'old_path': item.get('old_path', item['new_path']),  # SVN通常old_path和new_path相同
            'additions': item.get('additions', 0),
            'deletions': item.get('deletions', 0),
            # 保留SVN特有字段
            'action': item.get('action', 'M'),  # SVN操作类型：A(新增), M(修改), D(删除), R(替换)
            'is_binary': item.get('is_binary', False),
            'old_revision': item.get('old_revision'),
            'new_revision': item.get('new_revision')
        }
        for item in filter_deleted_files_changes
        if any(item.get('new_path', '').endswith(ext) for ext in supported_extensions)
    ]
    
    logger.info(f"After filtering by extension: {len(filtered_changes)} files")
    return filtered_changes


def slugify_url(original_url: str) -> str:
    """
    将原始URL转换为适合作为文件名的字符串，其中非字母或数字的字符会被替换为下划线
    
    Args:
        original_url: 原始URL
        
    Returns:
        处理后的字符串
    """
    # 移除URL协议部分
    original_url = re.sub(r'^https?://', '', original_url)
    original_url = re.sub(r'^file://', '', original_url)
    original_url = re.sub(r'^svn://', '', original_url)
    
    # 将非字母数字字符替换为下划线
    target = re.sub(r'[^a-zA-Z0-9]', '_', original_url)
    
    # 移除开头和尾部的下划线
    target = target.strip('_')
    
    return target


class SVNCommitHandler:
    """SVN提交事件处理器"""
    
    def __init__(self, webhook_data: Dict):
        """
        初始化SVN提交处理器
        
        Args:
            webhook_data: SVN webhook数据
        """
        self.webhook_data = webhook_data
        self.event_type = None
        self.repository_info = None
        self.commit_info = None
        self.changes_data = []
        self.parse_event_data()
    
    def parse_event_data(self):
        """解析SVN webhook事件数据"""
        # 解析事件类型
        self.event_type = self.webhook_data.get('event_type')
        
        # 解析仓库信息
        self.repository_info = self.webhook_data.get('repository', {})
        
        # 解析提交信息
        commits = self.webhook_data.get('commits', [])
        if commits:
            self.commit_info = commits[0]  # 通常SVN每次只有一个提交
        
        # 解析变更数据
        self.changes_data = self.webhook_data.get('changes', [])
        
        logger.info(f"Parsed SVN event: type={self.event_type}, "
                   f"repository={self.repository_info.get('name', 'unknown')}, "
                   f"changes={len(self.changes_data)}")
    
    def get_commit_changes(self) -> List[Dict]:
        """
        获取SVN提交的变更信息
        
        Returns:
            变更文件列表
        """
        if self.event_type not in ['Post-Commit', 'Pre-Commit']:
            logger.warn(f"Invalid SVN event type: {self.event_type}. "
                       f"Only 'Post-Commit' and 'Pre-Commit' are supported.")
            return []
        
        if not self.changes_data:
            logger.info("No changes found in SVN commit event.")
            return []
        
        logger.info(f"Retrieved {len(self.changes_data)} changes from SVN {self.event_type} event")
        return self.changes_data
    
    def get_commit_info(self) -> Optional[Dict]:
        """
        获取SVN提交信息（从commits数组获取，仅在Post-Commit事件中可用）
        
        Returns:
            提交信息字典或None
        """
        if not self.commit_info:
            logger.warn("No commit information found in SVN webhook data")
            return None
        
        return {
            'revision': self.commit_info.get('id'),
            'message': self.commit_info.get('message', ''),
            'author': self.commit_info.get('author', 'unknown'),
            'timestamp': self.commit_info.get('timestamp'),
            'url': self.commit_info.get('url', ''),
        }
    
    def get_event_attributes(self) -> Optional[Dict]:
        """
        获取SVN事件属性信息（从object_attributes获取，适用于Pre-Commit和Post-Commit事件）
        
        Returns:
            事件属性信息字典或None
        """
        object_attributes = self.webhook_data.get('object_attributes')
        if not object_attributes:
            logger.warn("No object_attributes found in SVN webhook data")
            return None
        
        return {
            'revision': object_attributes.get('revision'),
            'author': object_attributes.get('author', 'unknown'),
            'action': object_attributes.get('action', ''),
            'state': object_attributes.get('state', 'unknown'),
            'timestamp': object_attributes.get('created_at') or object_attributes.get('updated_at'),
            'message': object_attributes.get('message', ''),
            'target_branch': object_attributes.get('target_branch', 'trunk'),
            'source_branch': object_attributes.get('source_branch', 'trunk'),
        }
    
    def get_repository_info(self) -> Optional[Dict]:
        """
        获取SVN仓库信息
        
        Returns:
            仓库信息字典或None
        """
        if not self.repository_info:
            return None
        
        return {
            'uuid': self.repository_info.get('uuid'),
            'name': self.repository_info.get('name'),
            'url': self.repository_info.get('url'),
            'homepage': self.repository_info.get('homepage', ''),
        }
    
    def add_commit_notes(self, review_result: str):
        """
        添加SVN提交评审结果
        
        Args:
            review_result: 代码评审结果
            
        Note:
            SVN没有像GitLab/GitHub那样的评论系统，这里预留接口
        """
        # TODO: 实现SVN评审结果反馈机制
        # 可能的方案：
        # 1. 邮件通知提交者
        # 2. 企业IM通知
        # 3. 写入特定的日志文件
        # 4. 集成到现有的通知系统
        
        commit_info = self.get_commit_info()
        repo_info = self.get_repository_info()
        
        logger.info(f"SVN commit review result for repository '{repo_info.get('name') if repo_info else 'unknown'}', "
                   f"revision '{commit_info.get('revision') if commit_info else 'unknown'}': "
                   f"Ready to notify but implementation pending")
        logger.debug(f"Review result content: {review_result}")
        
        # 暂时抛出未实现异常，等待后续具体实现
        raise NotImplementedError(
            "SVN commit notes feature is not implemented yet. "
            "This method should be implemented based on specific notification requirements "
            "(email, IM, logging, etc.)"
        )
    
    def is_pre_commit_event(self) -> bool:
        """判断是否为Pre-Commit事件"""
        return self.event_type == 'Pre-Commit'
    
    def is_post_commit_event(self) -> bool:
        """判断是否为Post-Commit事件"""
        return self.event_type == 'Post-Commit'
    
    def get_svn_specific_info(self) -> Optional[Dict]:
        """
        获取SVN特有的信息
        
        Returns:
            SVN特有信息字典
        """
        return self.webhook_data.get('svn_info', {})
