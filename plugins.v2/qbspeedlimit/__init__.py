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

import pytz
import time


class QbSpeedLimit(_PluginBase):
    # 插件名称
    plugin_name = "QB智能限速"
    # 插件描述
    plugin_desc = "指定Tracker、站点等限速"
    # 插件图标
    plugin_icon = "Qbittorrent_A.png"
    # 插件版本
    plugin_version = "0.1"
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
    _enabled: bool = False
    _notify: bool = False
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
            self._enabled = config.get("enabled", True)
            self._notify = config.get("notify", False)
            self._upload_limit = config.get("upload_limit", 100)
        else:
            self._enabled = True
            self._notify = False
            self._upload_limit = 100
            
        # 初始化下载器服务
        if self._enabled:
            self._qb = self.downloader_helper.get_services()
            
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
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，参考qbcommand插件风格
        """
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
                                            "model": "upload_limit",
                                            "label": "上传限速 KB/s (仅对匹配tracker生效)",
                                            "placeholder": "KB/s",
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
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "本插件会定时遍历所有QB种子，若tracker包含www.hdkyl.in则自动限速。后续可扩展更多规则。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": True,
            "notify": False,
            "upload_limit": 100,
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
        # 每5分钟执行一次限速规则
        return [
            {
                "id": "QbSpeedLimitByTracker",
                "name": "QB按Tracker限速",
                "trigger": CronTrigger.from_crontab("*/5 * * * *"),
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
        遍历所有QB种子，若tracker包含www.hdkyl.in，则限速上传
        """
        if not self._enabled:
            return
            
        # 检查配置是否有效
        if not self._upload_limit or not str(self._upload_limit).isdigit():
            logger.error(f"上传限速值无效: {self._upload_limit}")
            return
            
        # 转换为KB/s的字节值 (1 KB = 1024 bytes)
        upload_limit_bytes = int(self._upload_limit) * 1024
        
        # 遍历所有下载器实例及其种子
        if not self._qb:
            self._qb = self.downloader_helper.get_services()
        if not self._qb:
            logger.error("未获取到qbittorrent服务实例")
            return
            
        tracker_matched_count = 0
        for service_name, service in self._qb.items():
            downloader = service.instance
            # 跳过非qbittorrent下载器
            if not self.downloader_helper.is_downloader("qbittorrent", service=service):
                logger.debug(f"下载器 {service_name} 不是qbittorrent，跳过")
                continue
                
            all_torrents, error = downloader.get_torrents()
            if error:
                logger.error(f"获取下载器 {service_name} 的种子失败: {error}")
                continue
                
            for torrent in all_torrents:
                # 获取tracker
                tracker = torrent.get("tracker")
                if not tracker:
                    continue
                    
                # 检查tracker是否匹配规则
                if "www.hdkyl.in" in tracker:
                    try:
                        torrent_hash = torrent.get("hash")
                        # 获取当前限速值
                        current_limit = 0
                        try:
                            # 部分qBittorrent客户端API可能不同
                            if hasattr(downloader, 'get_torrent_limits'):
                                _, current_limit = downloader.get_torrent_limits(torrent_hash)
                            elif hasattr(downloader, 'get_torrent_upload_limit'):
                                current_limit = downloader.get_torrent_upload_limit(torrent_hash)
                        except:
                            pass
                            
                        # 只有当前限速与设定不同时才设置
                        if current_limit != upload_limit_bytes:
                            # 设置上传限速
                            if hasattr(downloader, 'set_torrent_limits'):
                                downloader.set_torrent_limits(torrent_hash, up_limit=upload_limit_bytes)
                            elif hasattr(downloader, 'set_torrent_upload_limit'):
                                downloader.set_torrent_upload_limit(torrent_hash, upload_limit_bytes)
                                
                            logger.info(f"已为种子 {torrent.get('name')} ({torrent_hash}) 设置上传限速 {self._upload_limit} KB/s")
                            tracker_matched_count += 1
                            
                            # 发送通知
                            if self._notify:
                                self.post_message(
                                    mtype=NotificationType.SiteMessage,
                                    title=f"【QB智能限速】",
                                    text=f"已为种子 {torrent.get('name')} 设置上传限速 {self._upload_limit} KB/s"
                                )
                    except Exception as e:
                        logger.error(f"设置种子 {torrent.get('name')} 上传限速失败: {str(e)}")
        
        if tracker_matched_count > 0:
            logger.info(f"本次共设置了 {tracker_matched_count} 个种子的上传限速为 {self._upload_limit} KB/s")
