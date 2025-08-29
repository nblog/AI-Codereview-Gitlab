import json
import os
import time
from typing import Optional, Dict, Any

import requests

from biz.utils.log import logger


class DingTalkNotifier:
    def __init__(self):
        self.enabled = os.environ.get('DINGTALK_ENABLED_ROBOT', '0') == '1'
        self.app_key = os.environ.get('DINGTALK_APP_KEY')
        self.app_secret = os.environ.get('DINGTALK_APP_SECRET')
        self.robot_code = os.environ.get('DINGTALK_ROBOT_CODE')
        
        # 内存缓存access_token
        self._access_token = None
        self._token_expire_time = 0
        
        # API endpoints
        self.token_url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        self.search_user_url = "https://api.dingtalk.com/v1.0/contact/users/search"
        self.send_ding_url = "https://api.dingtalk.com/v1.0/robot/ding/send"

    def _get_access_token(self) -> Optional[str]:
        """
        获取access_token，带缓存机制
        :return: access_token 或 None
        """
        # 检查缓存的token是否还有效（提前30秒过期以防网络延迟）
        current_time = time.time()
        if self._access_token and current_time < (self._token_expire_time - 30):
            return self._access_token

        try:
            headers = {
                "Content-Type": "application/json"
            }
            data = {
                "appKey": self.app_key,
                "appSecret": self.app_secret
            }
            
            response = requests.post(url=self.token_url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            if 'accessToken' in result:
                self._access_token = result['accessToken']
                # 设置过期时间（API返回的是秒数，转换为时间戳）
                expire_in = result.get('expireIn', 7200)  # 默认7200秒
                self._token_expire_time = current_time + expire_in
                
                logger.info(f"钉钉access_token获取成功，有效期: {expire_in}秒")
                return self._access_token
            else:
                logger.error(f"钉钉access_token获取失败，响应: {result}")
                return None
                
        except Exception as e:
            logger.error(f"钉钉access_token获取异常: {e}")
            return None

    def _search_user_id(self, author: str) -> Optional[str]:
        """
        根据用户名搜索用户ID
        :param author: 用户名
        :return: 用户ID 或 None
        """
        access_token = self._get_access_token()
        if not access_token:
            logger.error("无法获取access_token，跳过用户搜索")
            return None

        try:
            headers = {
                "Content-Type": "application/json",
                "x-acs-dingtalk-access-token": access_token
            }
            data = {
                "queryWord": author,
                "offset": 0,
                "size": 1
            }
            
            response = requests.post(url=self.search_user_url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            if result.get('totalCount', 0) > 0 and result.get('list'):
                user_id = result['list'][0]
                logger.info(f"找到用户 '{author}' 的ID: {user_id}")
                return user_id
            else:
                logger.warning(f"未找到用户 '{author}'")
                return None
                
        except Exception as e:
            logger.error(f"搜索用户 '{author}' 异常: {e}")
            return None

    def _send_ding_message(self, content: str, receiver_user_ids: list) -> bool:
        """
        发送DING消息
        :param content: 消息内容
        :param receiver_user_ids: 接收者用户ID列表
        :return: 发送是否成功
        """
        access_token = self._get_access_token()
        if not access_token:
            logger.error("无法获取access_token，跳过消息发送")
            return False

        try:
            headers = {
                "Content-Type": "application/json",
                "x-acs-dingtalk-access-token": access_token
            }
            data = {
                "robotCode": self.robot_code,
                "remindType": 1,  # 1表示DING消息
                "receiverUserIdList": receiver_user_ids,
                "content": content
            }
            
            response = requests.post(url=self.send_ding_url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            if 'openDingId' in result:
                logger.info(f"钉钉DING消息发送成功! openDingId: {result['openDingId']}")
                if result.get('failedList'):
                    logger.warning(f"部分用户发送失败: {result['failedList']}")
                return True
            else:
                logger.error(f"钉钉DING消息发送失败，响应: {result}")
                return False
                
        except Exception as e:
            logger.error(f"钉钉DING消息发送异常: {e}")
            return False

    def send_message(self, content: str, msg_type='text', title='通知', is_at_all=False, 
                    project_name=None, url_slug=None, author=None):
        """
        发送钉钉DING通知消息
        :param content: 消息内容
        :param msg_type: 消息类型（保持接口兼容性，但实际只支持text）
        :param title: 消息标题（保持接口兼容性）
        :param is_at_all: 是否@所有人（保持接口兼容性）
        :param project_name: 项目名称（保持接口兼容性）
        :param url_slug: URL slug（保持接口兼容性）
        :param author: 作者用户名，用于搜索对应的用户ID
        """
        if not self.enabled:
            logger.info("钉钉机器人推送未启用")
            return

        if not all([self.app_key, self.app_secret, self.robot_code]):
            logger.error("钉钉机器人配置不完整，请检查 DINGTALK_APP_KEY、DINGTALK_APP_SECRET、DINGTALK_ROBOT_CODE 环境变量")
            return

        # 如果提供了author，搜索对应的用户ID
        if author:
            user_id = self._search_user_id(author)
            if not user_id:
                logger.warning(f"未找到用户 '{author}'，不发送DING消息")
                return
            
            receiver_user_ids = [user_id]
        else:
            logger.warning("未提供author参数，无法确定消息接收者，不发送DING消息")
            return

        # 发送DING消息
        success = self._send_ding_message(content, receiver_user_ids)
        if success:
            logger.info(f"钉钉DING消息发送成功，接收者: {receiver_user_ids}")
        else:
            logger.error(f"钉钉DING消息发送失败，接收者: {receiver_user_ids}")
