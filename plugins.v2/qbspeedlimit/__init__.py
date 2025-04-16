from typing import List, Tuple, Dict, Any, Optional
from enum import Enum
from urllib.parse import urlparse
import urllib
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo, MessageChannel, Notification
from app.schemas.types import EventType
from apscheduler.triggers.cron import CronTrigger
from app.core.event import eventmanager, Event
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import settings
from app.helper.sites import SitesHelper
from app.db.site_oper import SiteOper
from app.utils.string import StringUtils
from app.helper.downloader import DownloaderHelper
from datetime import datetime, timedelta
from pathlib import Path
import re
import uuid
import json

import pytz
import time


class SpeedLimitRule:
    def __init__(self, rule_id: str = None, name: str = "", tracker_pattern: str = "", 
                 upload_limit: int = 0, download_limit: int = 0, 
                 enable_upload_limit: bool = False, enable_download_limit: bool = False, 
                 enabled: bool = True):
        self.rule_id = rule_id or str(uuid.uuid4())
        self.name = name
        self.tracker_pattern = tracker_pattern
        self.upload_limit = upload_limit
        self.download_limit = download_limit
        self.enable_upload_limit = enable_upload_limit
        self.enable_download_limit = enable_download_limit
        self.enabled = enabled
        
    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "tracker_pattern": self.tracker_pattern,
            "upload_limit": self.upload_limit,
            "download_limit": self.download_limit,
            "enable_upload_limit": self.enable_upload_limit,
            "enable_download_limit": self.enable_download_limit,
            "enabled": self.enabled
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SpeedLimitRule':
        return cls(
            rule_id=data.get("rule_id"),
            name=data.get("name", ""),
            tracker_pattern=data.get("tracker_pattern", ""),
            upload_limit=data.get("upload_limit", 0),
            download_limit=data.get("download_limit", 0),
            enable_upload_limit=data.get("enable_upload_limit", False),
            enable_download_limit=data.get("enable_download_limit", False),
            enabled=data.get("enabled", True)
        )
    
    def match_tracker(self, tracker: str) -> bool:
        """
        检查tracker是否匹配规则表达式
        """
        if not self.tracker_pattern or not self.enabled:
            return False
        try:
            return bool(re.search(self.tracker_pattern, tracker))
        except re.error:
            # 如果正则表达式无效，尝试使用简单的字符串包含检查
            return self.tracker_pattern in tracker


class QbSpeedLimit(_PluginBase):
    # 插件名称
    plugin_name = "QB智能限速"
    # 插件描述
    plugin_desc = "指定Tracker、站点等限速"
    # 插件图标
    plugin_icon = "Qbittorrent_A.png"
    # 插件版本
    plugin_version = "0.2"
    # 插件作者
    plugin_author = "JavaZeroo"
    # 作者主页
    author_url = "https://github.com/JavaZeroo"
    # 插件配置项ID前缀
    plugin_config_prefix = "qbspeedlimit_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _sites = None
    _siteoper = None
    _qb = None
    _enabled = False
    _onlyonce = False
    _notify = False
    _pause_cron = None
    _resume_cron = None
    _only_pause_once = False
    _only_resume_once = False
    _only_pause_upload = False
    _only_pause_download = False
    _only_pause_checking = False
    _upload_limit = 0
    _enable_upload_limit = False
    _download_limit = 0
    _enable_download_limit = False
    _op_site_ids = []
    _op_sites = []
    _multi_level_root_domain = ["edu.cn", "com.cn", "net.cn", "org.cn"]
    _scheduler = None
    _exclude_dirs = ""
    _rules = []
    _cron = ""  # 默认为空，等待用户设置

    def init_plugin(self, config: dict = None):
        """
        生效配置信息
        :param config: 配置信息字典
        """
        self._sites = SitesHelper()
        self._siteoper = SiteOper()
        self.downloader_helper = DownloaderHelper()
        # 停止现有任务
        self.stop_service()
        
        # 读取配置
        if config:
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", False)
            self._upload_limit = config.get("upload_limit", 100)
            self._cron = config.get("cron", "")
            
            # 加载规则
            rules_data = config.get("rules", [])
            self._rules = [SpeedLimitRule.from_dict(rule_data) for rule_data in rules_data]
            
            # 确保默认有一条规则
            if not self._rules:
                default_rule = SpeedLimitRule(
                    name="默认规则",
                    tracker_pattern="www.hdkyl.in",
                    upload_limit=100,
                    enable_upload_limit=True,
                    enabled=False
                )
                self._rules.append(default_rule)
        else:
            self._enabled = False
            self._notify = False
            self._onlyonce = False
            self._upload_limit = 100
            self._cron = ""
            # 创建一条默认规则
            default_rule = SpeedLimitRule(
                name="默认规则",
                tracker_pattern="www.hdkyl.in",
                upload_limit=100,
                enable_upload_limit=True,
                enabled=False
            )
            self._rules = [default_rule]

        if self._onlyonce:
            self._onlyonce = False
            # 立即执行一次限速规则
            self.apply_speed_limit_by_tracker()
            logger.info("QB智能限速插件已执行一次限速规则")
            self.__update_config()
        logger.info("QB智能限速插件已初始化")

    def get_state(self) -> bool:
        """
        获取插件运行状态
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册插件远程命令
        [{
            "cmd": "/xx",
            "event": EventType.xx,
            "desc": "名称",
            "category": "分类，需要注册到Wechat时必须有分类",
            "data": {}
        }]
        """
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        注册插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API名称",
            "description": "API说明"
        }]
        """
        return [
            {
                "path": "/rules",
                "endpoint": self.api_get_rules,
                "methods": ["GET"],
                "summary": "获取限速规则",
                "description": "获取所有限速规则"
            },
            {
                "path": "/rule",
                "endpoint": self.api_add_rule,
                "methods": ["POST"],
                "summary": "添加限速规则",
                "description": "添加新的限速规则"
            },
            {
                "path": "/rule/{rule_id}",
                "endpoint": self.api_update_rule,
                "methods": ["PUT"],
                "summary": "更新限速规则",
                "description": "更新指定限速规则"
            },
            {
                "path": "/rule/{rule_id}",
                "endpoint": self.api_delete_rule,
                "methods": ["DELETE"],
                "summary": "删除限速规则",
                "description": "删除指定限速规则"
            },
            {
                "path": "/run",
                "endpoint": self.api_run_once,
                "methods": ["POST"],
                "summary": "执行一次限速",
                "description": "立即执行一次限速规则"
            }
        ]

    def api_get_rules(self):
        """获取所有限速规则"""
        return [rule.to_dict() for rule in self._rules]

    def api_add_rule(self, request_data):
        """添加新的限速规则"""
        rule = SpeedLimitRule.from_dict(request_data)
        self._rules.append(rule)
        self.__update_config()
        return {"code": 0, "msg": "添加成功", "rule": rule.to_dict()}

    def api_update_rule(self, rule_id, request_data):
        """更新指定限速规则"""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                updated_rule = SpeedLimitRule.from_dict({**request_data, "rule_id": rule_id})
                self._rules[i] = updated_rule
                self.__update_config()
                return {"code": 0, "msg": "更新成功", "rule": updated_rule.to_dict()}
        return {"code": 1, "msg": "规则不存在"}

    def api_delete_rule(self, rule_id):
        """删除指定限速规则"""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                del self._rules[i]
                self.__update_config()
                return {"code": 0, "msg": "删除成功"}
        return {"code": 1, "msg": "规则不存在"}

    def api_run_once(self):
        """立即执行一次限速规则"""
        self.apply_speed_limit_by_tracker()
        return {"code": 0, "msg": "执行成功"}

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，参考qbcommand插件风格
        """
        rules_table = {
            "component": "VRow",
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [
                        {
                            "component": "VDataTable",
                            "props": {
                                "headers": [
                                    {"title": "规则名称", "key": "name", "sortable": True},
                                    {"title": "Tracker匹配", "key": "tracker_pattern"},
                                    {"title": "上传限速", "key": "upload_limit"},
                                    {"title": "下载限速", "key": "download_limit"},
                                    {"title": "启用", "key": "enabled"},
                                    {"title": "操作", "key": "actions", "sortable": False}
                                ],
                                "items": [rule.to_dict() for rule in self._rules],
                                "item-key": "rule_id",
                                "show-select": False,
                                "disable-pagination": True,
                                "hide-default-footer": True
                            },
                            "slots": {
                                "item.actions": {
                                    "component": "VBtn",
                                    "props": {
                                        "x-small": True,
                                        "text": True,
                                        "@click": "editRule(item)"
                                    },
                                    "content": "编辑"
                                },
                                "item.enabled": {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "item.enabled",
                                        "small": True
                                    }
                                }
                            }
                        }
                    ]
                }
            ]
        }

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次"
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    { 
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "监测周期",
                                            "placeholder": "5位cron表达式，默认每5分钟"
                                        }
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "本插件会根据配置的规则定时遍历所有QB种子，若tracker匹配规则则自动限速。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VDivider",
                        "props": {"class": "my-4"}
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSubheader",
                                        "props": {"class": "text-h6"},
                                        "content": "限速规则配置"
                                    }
                                ]
                            }
                        ]
                    },
                    # 这里添加规则表格
                    rules_table,
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "class": "d-flex justify-end"},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "primary",
                                            "@click": "openAddRuleDialog"
                                        },
                                        "content": "添加规则"
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        ], {
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "upload_limit": self._upload_limit,
            "cron": self._cron,
            "rules": [rule.to_dict() for rule in self._rules]
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        插件详情页面使用Vuetify组件拼装，参考：https://vuetifyjs.com/
        """
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务，定时执行限速规则
        """
        # 使用配置的cron表达式
        cron_expression = self._cron if self._cron else None
        return [
            {
                "id": "QbSpeedLimitByTracker",
                "name": "QB按Tracker限速",
                "trigger": CronTrigger.from_crontab(cron_expression),
                "func": self.apply_speed_limit_by_tracker,
                "kwargs": {},
            }
        ]

    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """
        获取插件仪表盘页面，需要返回：1、仪表板col配置字典；2、全局配置（自动刷新等）；3、仪表板页面元素配置json（含数据）
        1、col配置参考：
        {
            "cols": 12, "md": 6
        }
        2、全局配置参考：
        {
            "refresh": 10, // 自动刷新时间，单位秒
            "border": True, // 是否显示边框，默认True，为False时取消组件边框和边距，由插件自行控制
            "title": "组件标题", // 组件标题，如有将显示该标题，否则显示插件名称
            "subtitle": "组件子标题", // 组件子标题，缺省时不展示子标题
        }
        3、页面配置使用Vuetify组件拼装，参考：https://vuetifyjs.com/

        kwargs参数可获取的值：1、user_agent：浏览器UA

        :param key: 仪表盘key，根据指定的key返回相应的仪表盘数据，缺省时返回一个固定的仪表盘数据（兼容旧版）
        """
        pass

    def get_dashboard_meta(self) -> Optional[List[Dict[str, str]]]:
        """
        获取插件仪表盘元信息
        返回示例：
            [{
                "key": "dashboard1", // 仪表盘的key，在当前插件范围唯一
                "name": "仪表盘1" // 仪表盘的名称
            }, {
                "key": "dashboard2",
                "name": "仪表盘2"
            }]
        """
        pass

    def stop_service(self):
        """
        停止插件
        """
        pass

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "notify": self._notify,
            "upload_limit": self._upload_limit,
            "cron": self._cron,
            "rules": [rule.to_dict() for rule in self._rules]
        })


    def update_config(self, config: dict, plugin_id: str = None) -> bool:
        """
        更新配置信息
        :param config: 配置信息字典
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.systemconfig.set(f"plugin.{plugin_id}", config)

    def get_config(self, plugin_id: str = None) -> Any:
        """
        获取配置信息
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.systemconfig.get(f"plugin.{plugin_id}")

    def get_data_path(self, plugin_id: str = None) -> Path:
        """
        获取插件数据保存目录
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        data_path = settings.PLUGIN_DATA_PATH / f"{plugin_id}"
        if not data_path.exists():
            data_path.mkdir(parents=True)
        return data_path

    def save_data(self, key: str, value: Any, plugin_id: str = None):
        """
        保存插件数据
        :param key: 数据key
        :param value: 数据值
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        self.plugindata.save(plugin_id, key, value)

    def get_data(self, key: str = None, plugin_id: str = None) -> Any:
        """
        获取插件数据
        :param key: 数据key
        :param plugin_id: plugin_id
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.plugindata.get_data(plugin_id, key)

    def del_data(self, key: str, plugin_id: str = None) -> Any:
        """
        删除插件数据
        :param key: 数据key
        :param plugin_id: plugin_id
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.plugindata.del_data(plugin_id, key)

    def post_message(self, channel: MessageChannel = None, mtype: NotificationType = None, title: str = None,
                     text: str = None, image: str = None, link: str = None, userid: str = None, username: str = None):
        """
        发送消息
        """
        if not link:
            link = settings.MP_DOMAIN(f"#/plugins?tab=installed&id={self.__class__.__name__}")
        self.chain.post_message(Notification(
            channel=channel, mtype=mtype, title=title, text=text,
            image=image, link=link, userid=userid, username=username
        ))

    def close(self):
        pass

    def apply_speed_limit_by_tracker(self):
        """
        遍历所有QB种子，根据规则进行限速
        """
        if not self._enabled:
            return
            
        # 遍历所有下载器实例及其种子
        if not self._qb:
            self._qb = self.downloader_helper.get_services()
        if not self._qb:
            logger.error("未获取到qbittorrent服务实例")
            return

        for service_name, service in self._qb.items():
            downloader = service.instance
            # 跳过非qbittorrent下载器
            if not self.downloader_helper.is_downloader("qbittorrent", service=service):
                logger.info(f"下载器 {service_name} 不是qbittorrent，跳过")
                continue
                
            all_torrents, error = downloader.get_torrents()
            # 计时
            start_time = time.time()
            logger.info(f"下载器 {service_name} 的种子数量: {len(all_torrents)}")
            rule_matched_count = 0
            if error:
                logger.error(f"获取下载器 {service_name} 的种子失败: {error}")
                continue

            for torrent in all_torrents:
                # 获取tracker
                tracker = torrent.get("tracker", "")
                if not tracker:
                    continue
                    
                # 检查是否匹配任何规则
                for rule in self._rules:
                    if not rule.enabled:
                        continue
                        
                    if rule.match_tracker(tracker):
                        torrent_hash = torrent.get("hash")
                        torrent_name = torrent.get("name")
                        changes_made = False
                        
                        # 设置上传限速
                        if rule.enable_upload_limit and rule.upload_limit > 0:
                            upload_limit_bytes = int(rule.upload_limit) * 1024
                            try:
                                # 获取当前上传限速
                                current_limit = 0
                                try:
                                    if hasattr(downloader.qbc, 'torrents_upload_limit'):
                                        current_limits = downloader.qbc.torrents_upload_limit(torrent_hashes=torrent_hash)
                                        if isinstance(current_limits, dict) and torrent_hash in current_limits:
                                            current_limit = current_limits.get(torrent_hash)
                                except Exception as e:
                                    logger.debug(f"获取种子 {torrent_name} 当前上传限速失败: {str(e)}")
                                
                                # 仅在必要时设置上传限速
                                if current_limit != upload_limit_bytes:
                                    downloader.qbc.torrents_set_upload_limit(torrent_hashes=torrent_hash, limit=upload_limit_bytes)
                                    logger.info(f"规则 '{rule.name}': 已为种子 {torrent_name} 设置上传限速 {rule.upload_limit} KB/s")
                                    changes_made = True
                            except Exception as e:
                                logger.error(f"设置种子 {torrent_name} 上传限速失败: {str(e)}")
                                
                        # 设置下载限速
                        if rule.enable_download_limit and rule.download_limit > 0:
                            download_limit_bytes = int(rule.download_limit) * 1024
                            try:
                                # 获取当前下载限速
                                current_limit = 0
                                try:
                                    if hasattr(downloader.qbc, 'torrents_download_limit'):
                                        current_limits = downloader.qbc.torrents_download_limit(torrent_hashes=torrent_hash)
                                        if isinstance(current_limits, dict) and torrent_hash in current_limits:
                                            current_limit = current_limits.get(torrent_hash)
                                except Exception as e:
                                    logger.debug(f"获取种子 {torrent_name} 当前下载限速失败: {str(e)}")
                                
                                # 仅在必要时设置下载限速
                                if current_limit != download_limit_bytes:
                                    downloader.qbc.torrents_set_download_limit(torrent_hashes=torrent_hash, limit=download_limit_bytes)
                                    logger.info(f"规则 '{rule.name}': 已为种子 {torrent_name} 设置下载限速 {rule.download_limit} KB/s")
                                    changes_made = True
                            except Exception as e:
                                logger.error(f"设置种子 {torrent_name} 下载限速失败: {str(e)}")
                        
                        if changes_made:
                            rule_matched_count += 1
                            # 发送通知
                            if self._notify:
                                limit_info = []
                                if rule.enable_upload_limit:
                                    limit_info.append(f"上传: {rule.upload_limit} KB/s")
                                if rule.enable_download_limit:
                                    limit_info.append(f"下载: {rule.download_limit} KB/s")
                                    
                                self.post_message(
                                    mtype=NotificationType.SiteMessage,
                                    title=f"【QB智能限速】规则: {rule.name}",
                                    text=f"已为种子 {torrent_name} 设置限速 ({', '.join(limit_info)})"
                                )
                        
                        # 一个种子只应用一条规则，匹配到了就跳出
                        break
                        
            stop_time = time.time()
            elapsed_time = stop_time - start_time
            logger.info(f"限速规则执行完成，耗时 {elapsed_time:.2f} 秒")
            if rule_matched_count > 0:
                logger.info(f"本次共对 {rule_matched_count} 个种子应用了限速规则")
